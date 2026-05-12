import { useCallback, useEffect, useRef, useState } from 'react'
import type { Position } from '../types'
import { clamp, fmt } from '../lib/utils'
import { useDebounced } from '../lib/hooks'
import { useEventLog } from '../context/EventLogContext'
import { usePartition } from '../context/PartitionContext'
import { RedisDot } from './RedisDot'

interface PositionBoxProps {
  badge: 'ts' | 'py'
  wsUrl: string
  restUrl: string
}

export function PositionBox({ badge, wsUrl, restUrl }: PositionBoxProps) {
  const { addEvent } = useEventLog()
  const { capMode, partitionSource, tsRedisConnected, pyRedisConnected, onTsStatus, onPyStatus } = usePartition()

  const redisConnected = badge === 'ts' ? tsRedisConnected : pyRedisConnected
  const onStatus = badge === 'ts' ? onTsStatus : onPyStatus
  const backendName = badge === 'ts' ? 'NestJS' : 'FastAPI'

  const [pos, setPos] = useState<Position | null>(null)
  const [rejected, setRejected] = useState(false)
  const canvasRef = useRef<HTMLDivElement>(null)
  const dragging = useRef(false)
  const prevPos = useRef<Position | null>(null)
  const onStatusRef = useRef(onStatus)
  onStatusRef.current = onStatus

  const persist = useCallback(async (p: Position) => {
    try {
      const res = await fetch(restUrl, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(p),
      })
      if (res.ok) {
        addEvent(`Write accepted by ${backendName}: ${fmt(p)}`, 'info')
      } else {
        addEvent(`Write REJECTED by ${backendName} (CP mode)`, 'error')
        setPos(prevPos.current)
        setRejected(true)
        setTimeout(() => setRejected(false), 400)
      }
    } catch {
      addEvent(`${backendName} unreachable`, 'error')
    }
  }, [restUrl, addEvent, backendName])

  const persistDebounced = useDebounced(persist, 10)

  const toNormalized = useCallback((clientX: number, clientY: number): Position => {
    const rect = canvasRef.current!.getBoundingClientRect()
    const x = clamp((clientX - rect.left) / rect.width * 2 - 1, -1, 1)
    const y = clamp((clientY - rect.top) / rect.height * 2 - 1, -1, 1)
    return { x: Math.round(x * 1000) / 1000, y: Math.round(y * 1000) / 1000 }
  }, [])

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    prevPos.current = pos
    dragging.current = true
    const onMove = (e: MouseEvent) => {
      if (!dragging.current) return
      const p = toNormalized(e.clientX, e.clientY)
      setPos(p)
      persistDebounced(p)
    }
    const onUp = () => {
      dragging.current = false
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [pos, toNormalized, persistDebounced])

  useEffect(() => {
    fetch(restUrl)
      .then(r => r.json())
      .then(d => { if (d) setPos({ x: d.x, y: d.y }) })
      .catch(() => setPos({ x: 0, y: 0 }))
  }, [restUrl]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let ws: WebSocket
    let retryTimer: ReturnType<typeof setTimeout>
    let dead = false

    const connect = () => {
      ws = new WebSocket(wsUrl)
      ws.onmessage = (e) => {
        const data = JSON.parse(e.data)
        if (data.type === 'status') {
          onStatusRef.current(data)
          return
        }
        if (dragging.current) return
        const p = { x: data.x as number, y: data.y as number }
        setPos(p)
        addEvent(`Replication received by ${backendName}: ${fmt(p)}`, 'info')
      }
      ws.onclose = () => {
        if (dead) return
        retryTimer = setTimeout(connect, 1500)
      }
      ws.onerror = () => ws.close()
    }

    connect()
    return () => {
      dead = true
      clearTimeout(retryTimer)
      ws?.close()
    }
  }, [wsUrl, addEvent, backendName]) // eslint-disable-line react-hooks/exhaustive-deps

  const left = pos ? `${(pos.x + 1) / 2 * 100}%` : '50%'
  const top  = pos ? `${(pos.y + 1) / 2 * 100}%` : '50%'

  return (
    <div className="position-box">
      <div className="box-header">
        <RedisDot connected={capMode !== 'normal' && partitionSource === 'manual' ? false : redisConnected} />
        <span className="box-title">{backendName}</span>
        <span className={`box-badge ${badge}`}>{badge}</span>
        {capMode !== 'normal' && (
          <span className={`status-pill ${capMode.toLowerCase()}`}>
            {capMode === 'AP' ? 'PARTITIONED / AP' : 'PARTITIONED / CP'}
          </span>
        )}
        <span className="box-coords">{pos ? `${pos.x.toFixed(3)}, ${pos.y.toFixed(3)}` : '…'}</span>
      </div>
      <div className={`box-canvas${rejected ? ' rejected-flash' : ''}`} ref={canvasRef}>
        <div className="crosshair-h" />
        <div className="crosshair-v" />
        {pos && <div className={`marker ${badge}`} style={{ left, top }} onMouseDown={onMouseDown} />}
      </div>
    </div>
  )
}
