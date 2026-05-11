# Changelog

Notable milestones in the development of this project. Entries are in reverse chronological order.

---

## Outbox pattern (May 7, 2026)

Both backends now use dedicated databases: NestJS on MongoDB, FastAPI on PostgreSQL. Previously, position state lived in memory and was replicated via a direct Redis publish after the write. If the process crashed between the successful write and the publish, the replication event was silently lost.

The outbox pattern closes that window: every write atomically inserts an outbox record in the same database transaction as the position update. A separate relay process reads the outbox and publishes to Redis; only on a confirmed publish does it delete the record. If the relay crashes or Redis is momentarily unreachable, the record survives and is retried on restart.

The two backends implement the relay differently:

- NestJS: MongoDB change stream (push-based CDC, near-zero latency)
- FastAPI: polling loop at 100ms intervals

Both achieve the same at-least-once delivery guarantee with different underlying mechanics.

---

## Real Redis partition and frontend polish (April 30, 2026)

Until this point the only partition mode was application-layer: the frontend POSTed a flag to both backends, which suppressed replication in software. Redis itself remained reachable.

A second, infrastructure-level partition mode was added, triggered by stopping the Redis service. Both backends detect the lost connection independently and enter partition mode automatically, without any frontend coordination. When Redis is restored, the frontend detects that both backends report `redis.connected: true` and runs the last-write-wins reconciliation flow automatically, without user input.

Auto-heal was removed from the simulated partition flow. Previously, the simulated partition would auto-reconcile on deactivation, which short-circuited the Heal button. Reconciliation for simulated partitions is now always user-driven.

The frontend was reworked in the same batch:

- App component structure refactored
- Partition control strip redesigned
- AP/CP mode presentation improved
- Environment variable handling cleaned up
- Diagram contrast fixed for light and dark backgrounds

---

## CAP theorem partition demo (April 28-29, 2026)

Both backends implement genuine data divergence during a simulated partition.

AP mode:

- Both backends accept writes independently
- Each broadcasts to its own WebSocket clients; neither replicates to the other
- The frontend shows both markers diverging in real time, with positions genuinely differing in each backend's database

CP mode:

- Both backends reject writes with 503
- Neither database changes, so neither marker can move
- Both views stay consistent with each other

Heal flow: the frontend reads each backend's local state, applies the chosen heuristic (last-write-wins, NestJS wins, or FastAPI wins), deactivates the partition, and PATCHes the winner's value to the winning backend. The winning backend writes it to its own storage, broadcasts to its own WebSocket clients, and publishes a replication event. The other backend receives it and converges. The heal uses the normal write and replication path.

An initial partition skeleton (the `/admin/partition` endpoint and in-process flag) had been added in early April, but data divergence, the heal flow, and the event log were all implemented here. `ARCHITECTURE.md` was added in this milestone.

---

## Observability (April 5-6, 2026)

Both backends expose Prometheus metrics:

- HTTP request counts and durations
- Active WebSocket connections
- Redis publish/receive counts

A monitoring stack deploys alongside the app in Kubernetes: kube-prometheus-stack (Prometheus and Grafana), a Loki and Alloy log pipeline, and a pre-built Grafana dashboard auto-imported via a ConfigMap sidecar. ServiceMonitors scrape both backends every 15 seconds.

Loki configuration required some iteration: `grafana/loki-stack` (Loki 2.6.1) is incompatible with modern Grafana due to a missing API endpoint added in 2.7; the fix was switching to `grafana/loki` (SingleBinary mode, Loki 3.x). Alloy replaced the deprecated Promtail for log collection. Access log format was standardized across both backends at the same time.

---

## Kubernetes (April 5, 2026)

Dockerfiles for all three services (NestJS, FastAPI, React/nginx). Kubernetes manifests deploy the full stack into the `edu-oe` namespace. A Makefile wraps the image build (targeting minikube's Docker daemon directly, no registry needed) and the kubectl apply sequence. All app services are exposed as LoadBalancer services; `minikube tunnel` maps them to the same localhost ports the frontend hardcodes, so no environment variable changes are needed for the k8s deployment.

---

## Replication (April 3-4, 2026)

- NestJS writes position updates to PostgreSQL and publishes to Redis; FastAPI subscribes and pushes updates to its WebSocket clients
- FastAPI publishes to Redis; NestJS subscribes and pushes updates to its WebSocket clients
- The frontend connected both WebSocket streams, showing both markers in sync under normal operation
- Loop prevention: each backend tags its events with a source identifier and ignores events that match its own tag

---

## Project foundation (April 3, 2026)

pnpm monorepo with three workspaces: a NestJS backend, a FastAPI backend, and a React/Vite frontend. Docker Compose brings up PostgreSQL and Redis. The frontend renders two draggable markers, one per backend, with no replication between them.
