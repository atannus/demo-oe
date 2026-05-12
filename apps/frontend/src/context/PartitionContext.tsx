import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import type { BackendStatus, CapMode, HealingHeuristic, LocalState } from '../types'
import { TS_REST, PY_REST } from '../lib/config'
import { fmt } from '../lib/utils'
import { useEventLog } from './EventLogContext'

interface PartitionContextValue {
  capMode: CapMode
  partitionSource: 'manual' | 'auto'
  healingHeuristic: HealingHeuristic
  autoPartitionMode: 'AP' | 'CP'
  tsRedisConnected: boolean | null
  pyRedisConnected: boolean | null
  setHealingHeuristic: (h: HealingHeuristic) => void
  onTrigger: () => void
  onHeal: () => void
  onPartitionModeChange: (mode: 'AP' | 'CP') => void
  onTsStatus: (status: BackendStatus) => void
  onPyStatus: (status: BackendStatus) => void
}

const PartitionContext = createContext<PartitionContextValue | null>(null)

export function PartitionProvider({ children }: { children: ReactNode }) {
  const { addEvent } = useEventLog()

  const [capMode, setCapMode] = useState<CapMode>('normal')
  const [partitionSource, setPartitionSource] = useState<'manual' | 'auto'>('manual')
  const [healingHeuristic, setHealingHeuristicState] = useState<HealingHeuristic>(
    () => (localStorage.getItem('healingHeuristic') as HealingHeuristic | null) ?? 'lww'
  )
  const [autoPartitionMode, setAutoPartitionModeState] = useState<'AP' | 'CP'>(
    () => (localStorage.getItem('partitionMode') as 'AP' | 'CP' | null) ?? 'AP'
  )
  const [tsStatus, setTsStatus] = useState<BackendStatus | null>(null)
  const [pyStatus, setPyStatus] = useState<BackendStatus | null>(null)

  const setHealingHeuristic = useCallback((h: HealingHeuristic) => {
    setHealingHeuristicState(h)
    localStorage.setItem('healingHeuristic', h)
  }, [])

  const heal = useCallback(async (heuristic: HealingHeuristic) => {
    addEvent('Heal initiated. Reading local state from both backends.', 'info')

    const [tsRes, pyRes] = await Promise.all([
      fetch(`${TS_REST}/admin/local-state`).then(r => r.json()) as Promise<LocalState | null>,
      fetch(`${PY_REST}/admin/local-state`).then(r => r.json()) as Promise<LocalState | null>,
    ])

    let winner: LocalState | null = null
    if (!tsRes && !pyRes) {
      addEvent('Both backends returned no state. Nothing to reconcile.', 'warn')
    } else if (!tsRes) {
      winner = pyRes
      addEvent('NestJS has no state. FastAPI wins by default.', 'warn')
    } else if (!pyRes) {
      winner = tsRes
      addEvent('FastAPI has no state. NestJS wins by default.', 'warn')
    } else {
      const tsT = new Date(tsRes.updated_at).getTime()
      const pyT = new Date(pyRes.updated_at).getTime()
      if (heuristic === 'lww') {
        winner = tsT >= pyT ? tsRes : pyRes
        const winnerName = tsT >= pyT ? 'NestJS' : 'FastAPI'
        addEvent(
          `LWW: NestJS ts=${tsRes.updated_at.slice(11, 23)} vs FastAPI ts=${pyRes.updated_at.slice(11, 23)}, ${winnerName} wins`,
          'success',
        )
      } else if (heuristic === 'ts-wins') {
        winner = tsRes
        addEvent('Heuristic: NestJS wins', 'success')
      } else {
        winner = pyRes
        addEvent('Heuristic: FastAPI wins', 'success')
      }
    }

    await Promise.all([
      fetch(`${TS_REST}/admin/partition`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ active: false, mode: 'AP' }) }),
      fetch(`${PY_REST}/admin/partition`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ active: false, mode: 'AP' }) }),
    ])
    addEvent('Partition deactivated on both backends', 'info')

    if (winner) {
      const winnerUrl = winner.source === 'ts' ? `${TS_REST}/position` : `${PY_REST}/position`
      const winnerName = winner.source === 'ts' ? 'NestJS' : 'FastAPI'
      await fetch(winnerUrl, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ x: winner.x, y: winner.y }),
      })
      addEvent(`Reconciled ${fmt(winner)} applied to ${winnerName}, replication propagating`, 'success')
    }

    setCapMode('normal')
  }, [addEvent])

  const onHeal = useCallback(() => heal(healingHeuristic), [heal, healingHeuristic])

  const onTrigger = useCallback(async () => {
    await Promise.all([
      fetch(`${TS_REST}/admin/partition`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ active: true, mode: autoPartitionMode }) }),
      fetch(`${PY_REST}/admin/partition`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ active: true, mode: autoPartitionMode }) }),
    ])
    setPartitionSource('manual')
    setCapMode(autoPartitionMode)
    addEvent(
      autoPartitionMode === 'AP'
        ? 'AP partition simulated. Replication suppressed, both backends accept writes.'
        : 'CP partition simulated. Writes rejected on both backends.',
      'warn',
    )
  }, [autoPartitionMode, addEvent])

  const onPartitionModeChange = useCallback(async (mode: 'AP' | 'CP') => {
    setAutoPartitionModeState(mode)
    localStorage.setItem('partitionMode', mode)
    await Promise.all([
      fetch(`${TS_REST}/admin/partition-config`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ autoMode: mode }) }),
      fetch(`${PY_REST}/admin/partition-config`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ autoMode: mode }) }),
    ])
  }, [])

  const onTsStatus = useCallback((status: BackendStatus) => setTsStatus(status), [])
  const onPyStatus = useCallback((status: BackendStatus) => setPyStatus(status), [])

  // Sync capMode from backend state on connect/reconnect
  useEffect(() => {
    if (!tsStatus || !pyStatus) return
    if (capMode !== 'normal') return
    const active = tsStatus.partition.active || pyStatus.partition.active
    if (active) {
      const status = tsStatus.partition.active ? tsStatus : pyStatus
      setCapMode(status.partition.mode)
      setPartitionSource(status.partition.source)
      const reason = status.partition.source === 'auto' ? 'Redis down' : 'backend state'
      addEvent(`Partition detected (${reason}). ${status.partition.mode} mode active.`, 'warn')
    }
  }, [tsStatus, pyStatus, capMode, addEvent])

  // Auto-heal when Redis reconnects after an infrastructure partition
  useEffect(() => {
    if (partitionSource !== 'auto') return
    if (capMode === 'normal') return
    if (!tsStatus || !pyStatus) return
    if (tsStatus.partition.active || pyStatus.partition.active) return
    if (!tsStatus.redis.connected || !pyStatus.redis.connected) return
    addEvent('Redis reconnected. Healing partition.', 'info')
    setPartitionSource('manual')
    onHeal()
  }, [tsStatus, pyStatus, partitionSource, capMode, addEvent, onHeal])

  return (
    <PartitionContext.Provider value={{
      capMode,
      partitionSource,
      healingHeuristic,
      autoPartitionMode,
      tsRedisConnected: tsStatus?.redis.connected ?? null,
      pyRedisConnected: pyStatus?.redis.connected ?? null,
      setHealingHeuristic,
      onTrigger,
      onHeal,
      onPartitionModeChange,
      onTsStatus,
      onPyStatus,
    }}>
      {children}
    </PartitionContext.Provider>
  )
}

export function usePartition(): PartitionContextValue {
  const ctx = useContext(PartitionContext)
  if (!ctx) throw new Error('usePartition must be used within PartitionProvider')
  return ctx
}
