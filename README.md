# interactive-cap-demo

A polyglot monorepo for CAP theorem experiments. Two independent backends (NestJS + FastAPI) act as symmetric multi-leader nodes, each owning their own database and staying in sync via Redis replication events. A React frontend shows each backend's live view of the shared state, lets you trigger AP or CP partitions, and walks you through reconciliation when the partition heals.

For the design rationale (why multi-leader, why separate databases, why the outbox pattern, why the partition model works the way it does), see [ARCHITECTURE.md](./ARCHITECTURE.md).

The diagram below shows the overall system topology:

![System Architecture](docs/diagrams/system-architecture.svg)

## Prerequisites

- Node.js + pnpm
- Python 3.11+ + uv
- Docker + Docker Compose
- minikube + kubectl + helm (for Kubernetes)

## Local development

Start Postgres, Redis, and MongoDB (as a single-node replica set), then all three services:

```bash
docker compose up -d
pnpm dev
```

| Service         | URL                   |
|-----------------|-----------------------|
| Frontend        | http://localhost:5173 |
| NestJS backend  | http://localhost:3001 |
| FastAPI backend | http://localhost:8000 |

## Kubernetes (minikube)

### First-time setup

```bash
minikube start
make build    # builds all three images into minikube's docker daemon
make deploy   # applies all k8s manifests
minikube tunnel  # separate terminal: maps LoadBalancer IPs to localhost
```

| Service         | URL                   |
|-----------------|-----------------------|
| Frontend        | http://localhost      |
| NestJS backend  | http://localhost:3001 |
| FastAPI backend | http://localhost:8000 |

### After making code changes

Because images are tagged `:latest` and `imagePullPolicy: Never`, Kubernetes won't detect that new code is available; you need to rebuild the image and explicitly restart the affected deployment(s).

**One service changed** (e.g. only frontend):
```bash
eval $(minikube docker-env)
docker build -t edu-oe/frontend:latest -f apps/frontend/Dockerfile .
kubectl rollout restart deployment/frontend -n edu-oe
```

**Multiple or all services changed:**
```bash
make build
kubectl rollout restart deployment/backend-ts deployment/backend-py deployment/frontend -n edu-oe
```

Wait for the rollout:
```bash
kubectl rollout status deployment/backend-ts deployment/backend-py deployment/frontend -n edu-oe
```

### Teardown

```bash
make teardown
```

## Observability (Prometheus + Grafana + Loki)

Requires minikube to be running with the app already deployed.

### Setup

```bash
make observe
```

This installs `kube-prometheus-stack`, `loki`, and `alloy` via Helm into the `monitoring` namespace, then applies the ServiceMonitors, Grafana dashboard ConfigMap, and Loki datasource ConfigMap.

### Accessing Grafana

With `minikube tunnel` running, Grafana is at **http://localhost:3000** (login: `admin` / `admin`).

The **edu-oe App Metrics** dashboard is auto-imported and shows:
- HTTP request rate and p95 latency per backend
- Active WebSocket connections
- Redis pub/sub message rates
- Live logs from the `edu-oe` namespace

The Loki datasource is provisioned automatically; no manual setup needed.

### Teardown

```bash
make observe-teardown
```
