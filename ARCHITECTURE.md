# Architecture & Design Decisions

This document explains the system's design: what it is, what it is trying to demonstrate, and the reasoning behind each architectural choice. It is intended for anyone who wants to understand the project at depth, not just run it.

## What this project is

A polyglot monorepo running two independent backends (NestJS + FastAPI) that maintain a shared logical state (the x, y position of a marker) and a React frontend that shows each backend's view of that state in real time. The purpose is to make the CAP theorem observable and interactive: users can introduce network partitions, watch the two backends' views diverge or freeze depending on the chosen consistency model, and then heal the partition and watch the system reconcile.

## The CAP theorem, briefly

![CAP Theorem Triangle](docs/diagrams/cap-triangle.svg)

The CAP theorem states that a distributed data system can provide at most two of three guarantees simultaneously:

- **C (Consistency)**: every read sees the most recent write (or an error)
- **A (Availability)**: every request receives a non-error response (data may be stale)
- **P (Partition tolerance)**: the system continues to operate when network links between nodes fail

Because real networks do fail, P is not optional in practice. The real choice is between C and A *when* a partition occurs:

- **CP systems**: sacrifice availability, rejecting operations on nodes that cannot confirm consistency with other nodes
- **AP systems**: sacrifice consistency, accepting writes and serving reads on every node even when different nodes temporarily see different data

## The multi-leader replication model

The system is designed as a **symmetric multi-leader** cluster:

- **NestJS** is one node. It owns a MongoDB database (`positions` collection).
- **FastAPI** is another node. It owns a PostgreSQL table (`positions`).
- Neither backend ever reads from or writes to the other's storage directly.
- **Redis** carries **replication events** between the two nodes via a durable outbox relay. When a backend writes to its own storage, it also inserts an outbox record in the same atomic transaction; a separate relay process reads the outbox and publishes `{ source, x, y, updated_at }` to the `position:replicated` channel. The other backend's subscriber receives this event, applies the value to its own storage, and notifies its own WebSocket clients.

The two backends use different databases intentionally: NestJS uses MongoDB and FastAPI uses PostgreSQL. This choice serves two purposes. First, it makes the boundary between nodes concrete and visible — each node's storage is structurally distinct, not just logically partitioned. Second, it allows the outbox relay strategies to differ across the two nodes, demonstrating the same pattern with different underlying mechanics (see below). The demo models two independent nodes that in a real deployment would live on separate servers, potentially in different geographies. Each container has its own process, network, and storage namespaces, which is the isolation that matters for demonstrating replication and CAP behavior. The only meaningful simplification is that both containers share a hardware failure domain: if the host goes down, both go down together. A real deployment would distribute them across independent infrastructure.

This is a real-world pattern. Systems like Apache Cassandra, DynamoDB Global Tables, and CockroachDB use multi-leader (or "multi-master") replication: writes are accepted at any node and replicated asynchronously to peers. The CAP tradeoff appears naturally when replication fails.

### Why multi-leader rather than primary-replica

A primary-replica design (one backend accepts writes, one applies them) would mean the replica's marker is never draggable (it only shows replicated state). That limits what the demo can show: the only interesting event is "can the replica serve reads when it loses the primary?" With multi-leader, both markers are independently draggable, both backends accept independent writes, and during a partition both markers can move to different positions. The divergence is visible and interactive, which makes the demonstration more effective.

## The replication channel

The diagram below shows the symmetric nature of the cluster: either backend can receive a write from the frontend. Writes are accepted at the nearest node; the writing backend is authoritative for that write.

![Architecture Data Flow](docs/diagrams/architecture-dataflow.svg)

The writing backend commits to its own table and broadcasts to its own WebSocket clients immediately, without waiting for replication to complete. This is intentional: delaying the local broadcast until after the Redis round-trip would introduce unnecessary latency visible to the user as jitter. The other backend receives the write asynchronously; this small lag is observable on the frontend and is part of the demonstration.

![Write Sequence](docs/diagrams/sequence-write-normal.svg)

The replication event payload includes a `source` field (`'ts'` or `'py'`). Each backend discards events where `source` matches itself, preventing a replication loop: NestJS publishes with `source: 'ts'`; FastAPI's subscriber ignores events with `source: 'py'`; and vice versa.

## The outbox pattern

### The problem it solves

The original implementation published to Redis directly after writing to the database. These two operations were independent: if the process crashed, or Redis was momentarily unreachable, between the successful database write and the publish call, the event was silently lost. The other backend never received it, and the divergence was permanent until a user-initiated heal.

### How it works

Each backend now performs an **atomic write** on every position update:

1. Write the new position to its own storage.
2. Insert an outbox record containing the same payload (`x`, `y`, `updated_at`) into an outbox table or collection **in the same database transaction**.
3. Return success to the caller.

A separate **relay process** runs independently of the write path. It reads outbox records and publishes them to Redis. Only after a confirmed publish does it delete the record. If the relay fails mid-cycle — because Redis is down, because the process crashes, or for any other reason — the record remains in the outbox and will be retried on the next cycle or on restart. This gives at-least-once delivery for the relay step.

Because the position write and the outbox insert are in the same transaction, they either both succeed or both fail. There is no window where the position is written but no outbox record exists to trigger replication.

### Two relay strategies, one pattern

The two backends implement the outbox relay differently, demonstrating that the same guarantee can be achieved with different underlying mechanics:

**FastAPI / PostgreSQL — polling relay**

A background coroutine (`outbox_publisher`) wakes every 100 milliseconds, queries `outbox` for the oldest undelivered rows, publishes each to Redis, and deletes the row on confirmation. This is the simplest relay strategy: it requires no database-specific features, works identically on any relational database, and is straightforward to reason about. The tradeoff is a bounded delivery latency equal to the poll interval.

**NestJS / MongoDB — change stream relay**

A NestJS service (`OutboxRelayService`) opens a MongoDB **change stream** on the `outbox` collection filtered to insert operations. MongoDB delivers each new outbox document to the relay as it is inserted, rather than the relay polling for it. The relay publishes to Redis and deletes the document on confirmation. This is MongoDB's native CDC (change data capture) primitive: the database pushes events rather than the relay pulling them. Delivery latency is near-zero. The tradeoff is that it requires a MongoDB replica set (even a single-node one) and a persistent stream connection.

On startup, the relay performs a **catch-up scan** before opening the stream: it queries the outbox collection for any documents left from before the process started (from a previous crash mid-relay) and processes them first. The stream is opened before the scan to avoid a gap between the two phases.

![Outbox Write Flow](docs/diagrams/outbox-write-flow.svg)

### Outbox behavior during partitions

When `partition_active` is true — either because the user triggered a simulated partition or because Redis went down and the auto-partition mode activated — the relay drops outbox records rather than buffering them. This is intentional:

- For **simulated partitions**, the divergence is deliberate. Buffering and auto-delivering writes made during the partition would short-circuit the Heal button flow, which is the educational centerpiece of the demo. Writes during a simulated AP partition are not retroactively replicated; the user resolves the divergence manually via the chosen heuristic.
- For **infrastructure partitions** (Redis down), the behavior is the same: writes land on the local database and are not replicated after heal. The outbox's contribution in this case is narrower than in the process-crash scenario — it ensures that any outbox records that *were* written before the partition activated are not lost, but new records written during the partition are dropped once the relay clears them.

The outbox pattern's primary value here is for the failure scenario it was designed for: the window between a successful database write and a successful Redis publish when both systems are available. In that window, a process crash or transient Redis error no longer silently loses the replication event.

## Partition simulation

The system supports two modes of partition, selectable before the partition is triggered.

**Simulated partition** (application-layer): the frontend POSTs `{ active: true, mode: 'AP' | 'CP' }` to `/admin/partition` on both backends simultaneously. Each backend updates its in-process partition state. Redis is not touched; the simulation operates at the application layer.

**Infrastructure partition** (real): the Redis service is stopped at the infrastructure level (`docker compose stop redis` or, in Kubernetes, scaling the Redis deployment to zero). Both backends detect the lost connection independently and enter the pre-configured mode automatically. When Redis is restored, both backends detect the reconnection and the frontend auto-heals using last-write-wins without requiring user intervention — mirroring how real AP systems reconcile after a network partition heals.

In both modes, when a partition is active each backend:
- **Suppresses outgoing replication**: does not publish to Redis after a write
- **Discards incoming replication**: ignores Redis messages from the other backend

This produces the same observable effect as a real network partition: writes land only on the node that received them, and the other node's state drifts.

### AP mode (availability over consistency)

Both backends continue accepting writes. Each backend broadcasts updates to its own WebSocket clients immediately. Because replication is suppressed, the two backends' tables (and therefore the two markers on the frontend) diverge. The frontend visually shows this divergence in real time. `GET /position` returns a non-error response from both backends (available), but the values may differ (not consistent).

### CP mode (consistency over availability)

Both backends reject writes with HTTP 503. No new data is written to either table, so neither table changes and the two views remain identical (consistent). The system sacrifices availability (writes are refused) in exchange for the guarantee that no divergence occurs. Reads still succeed; CP systems typically allow reads because the data is known to be consistent (nothing has been written since the partition was activated).

### Why both backends participate in the partition simultaneously

In a real network partition, *all* nodes are affected: a partition splits the cluster, it does not selectively affect one node. Activating the partition on one backend only would produce asymmetric behavior that does not reflect reality. The frontend sends the activation to both backends in parallel so both enter the partitioned state together. In the infrastructure partition mode, symmetry emerges from the infrastructure rather than coordination: when Redis goes down, both backends independently lose connectivity to it. Detection timing differs — NestJS reacts within milliseconds via connection error events; FastAPI's async subscriber takes a few seconds longer — but the frontend enters partition mode as soon as either node detects the loss, because replication is severed the moment the first node can no longer reach Redis.

## Healing and reconciliation

When a **simulated partition** is healed, the frontend drives reconciliation:

1. Read the current local state from each backend (`GET /admin/local-state`). This returns each backend's own table value (which may differ after an AP partition).
2. Apply the user-selected heuristic to determine the winner.
3. Deactivate the partition on both backends in parallel (`POST /admin/partition { active: false }`).
4. PATCH the winner's value to the **winning backend's** `/position` endpoint.

Step 4 reuses the normal write path: the winning backend writes the winner value to its own table, broadcasts to its own WebSocket clients, and publishes a replication event to Redis. The other backend receives the replication event and applies it to its own table. Both backends converge on the winner's value. This means the heal itself demonstrates the replication path; it is not a special reconciliation bypass.

When an **infrastructure partition** heals (Redis comes back), the frontend detects that both backends report `redis.connected: true` and runs the same reconciliation flow automatically using last-write-wins. No user action is required; the event log narrates the outcome.

### Why the PATCH goes to the winner's backend

An alternative would be to PATCH both backends with the winner's value directly. This also works, but bypasses the replication path (both backends receive the value from the frontend rather than from each other). Sending it to one backend and letting replication carry it to the other is more correct: it demonstrates that after healing, the replication channel works again.

### Healing heuristics

Three heuristics are available for user-initiated heals, chosen before triggering a partition:

- **Last-write-wins (LWW)**: compare `updated_at` timestamps; the more recent write wins. This is the most common conflict resolution strategy in AP systems (used by Cassandra, DynamoDB, and Redis Cluster). It assumes clocks are reasonably synchronized and that recency is a proxy for intent.
- **NestJS wins**: NestJS's value is always applied, regardless of timestamp. Useful for demonstrating that reconciliation strategies are a design choice, not a mathematical truth.
- **FastAPI wins**: symmetric to NestJS wins.

LWW is the default because it is the strategy most commonly encountered in production AP systems and the one that requires the least explanation. Infrastructure-partition auto-heals always use LWW.

## What the frontend shows

The frontend has two `PositionBox` components, one connected to each backend. Under normal operation both boxes display the same marker position, staying in sync via replication. During a partition:

- **AP**: each box tracks its own backend's state independently. If you drag one marker, only that box moves; the other stays. Both markers are draggable. The boxes show different positions: genuine divergence.
- **CP**: neither box's marker can be moved (writes are rejected at the server). Attempting to drag a marker results in a 503 response; the marker reverts to its pre-drag position and a red flash appears on the canvas.

An event log panel narrates every event in the session: partition activation, write acceptances and rejections, replication suppression, heal initiation, heuristic decision, and reconciliation outcome. This makes the demo self-explanatory without requiring the presenter to narrate everything verbally.

Each backend also exposes a `GET /admin/status` endpoint that reports the current partition state and Redis connection health. The frontend receives status updates via the existing WebSocket connection (each backend pushes a status message on connect and on any state change) and shows a per-backend Redis health indicator, enabling the infrastructure partition mode to be demonstrated without any manual flag-setting.

## What this project does not demonstrate

This is a simulation, not a production distributed system. Some limitations worth being aware of:

- **No clock synchronization**: LWW depends on `updated_at` timestamps generated by each backend's local clock. In this single-machine setup, clocks are identical. In a real multi-region deployment, clock skew would be a real concern (addressed by systems like Google Spanner using TrueTime).
- **No actual network partition** in the simulated mode: the partition is an application-layer flag, not a real network split. Both backends can always reach Redis and their own databases (MongoDB for NestJS, PostgreSQL for FastAPI). The infrastructure partition mode uses a real Redis outage, but each backend retains full access to its own database throughout.
- **No quorum**: CP systems like ZooKeeper, etcd, and Raft-based systems use quorum reads and writes to distinguish majority and minority partitions. This demo does not implement quorum; CP mode simply rejects all writes unconditionally, which is correct for a two-node cluster where no quorum majority is possible.
- **No cross-node durability during partition in AP mode**: writes during an AP partition go to each backend's own storage (durable locally), but the other backend's storage is stale. Reconciliation on heal may overwrite writes made by the losing backend. This is expected and is part of what AP mode means. The outbox pattern does not change this: outbox records are dropped when partition is active, so writes during a partition are not retroactively replicated after healing (the Heal button handles that explicitly).
- **At-least-once, not exactly-once**: the outbox relay delivers replication events at least once. Because the position upsert is idempotent (same `x, y` applied twice is harmless), duplicate delivery is benign in practice, but a production system would additionally deduplicate on the subscriber side.
