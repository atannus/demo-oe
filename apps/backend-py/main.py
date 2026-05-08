import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Gauge
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel
from sqlalchemy import Column, Float, Integer, String, DateTime, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

logging.getLogger("uvicorn.access").disabled = True

_http_logger = logging.getLogger("http")
_http_logger.setLevel(logging.INFO)
_http_handler = logging.StreamHandler()
_http_handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
_http_logger.addHandler(_http_handler)
_http_logger.propagate = False

DATA_ID = "1"

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_USER = os.getenv("DB_USER", "app")
DB_PASSWORD = os.getenv("DB_PASSWORD", "app")
DB_NAME = os.getenv("DB_NAME", "app")
REPLICATION_CHANNEL = "position:replicated"

DATABASE_URL = (
    f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()


class PositionRow(Base):
    __tablename__ = "positions"
    data_id = Column(String, primary_key=True)
    x = Column(Float, nullable=False)
    y = Column(Float, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class OutboxRow(Base):
    __tablename__ = "outbox"
    id = Column(Integer, primary_key=True, autoincrement=True)
    data_id = Column(String, nullable=False)
    x = Column(Float, nullable=False)
    y = Column(Float, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


# Connected WebSocket clients
clients: set[WebSocket] = set()

partition_active: bool = False
partition_mode: str = "AP"  # "AP" | "CP"
partition_source: str = "manual"  # "manual" | "auto"
auto_partition_mode: str = "AP"
redis_connected: bool = True

ws_connections_active = Gauge("ws_connections_active", "Active WebSocket connections")
redis_messages_published_total = Counter("redis_messages_published_total", "Redis messages published")
redis_messages_received_total = Counter("redis_messages_received_total", "Redis messages received")


async def broadcast(payload: dict) -> None:
    text_data = json.dumps(payload)
    dead = set()
    for ws in clients:
        try:
            await ws.send_text(text_data)
        except Exception:
            dead.add(ws)
    clients.difference_update(dead)


async def broadcast_status() -> None:
    await broadcast({
        "type": "status",
        "partition": {"active": partition_active, "mode": partition_mode, "source": partition_source},
        "redis": {"connected": redis_connected},
    })


async def apply_replication(x: float, y: float, updated_at_str: str) -> None:
    ts = datetime.fromisoformat(updated_at_str)
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                """
                INSERT INTO positions (data_id, x, y, updated_at)
                VALUES (:data_id, :x, :y, :updated_at)
                ON CONFLICT (data_id) DO UPDATE
                SET x = EXCLUDED.x, y = EXCLUDED.y, updated_at = EXCLUDED.updated_at
                """
            ),
            {"data_id": DATA_ID, "x": x, "y": y, "updated_at": ts},
        )
        await session.commit()
    await broadcast({"x": x, "y": y, "updated_at": updated_at_str})


async def redis_subscriber():
    global redis_connected, partition_active, partition_mode, partition_source
    while True:
        r = None
        try:
            r = aioredis.Redis(host=REDIS_HOST, port=REDIS_PORT, socket_connect_timeout=2)
            pubsub = r.pubsub()
            await pubsub.subscribe(REPLICATION_CHANNEL)

            was_disconnected = not redis_connected
            redis_connected = True
            if was_disconnected:
                if partition_source == "auto":
                    partition_active = False
                    partition_source = "manual"
                await broadcast_status()

            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                if partition_active:
                    continue
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode()
                event = json.loads(data)
                if event.get("source") == "py":
                    continue
                redis_messages_received_total.inc()
                await apply_replication(event["x"], event["y"], event["updated_at"])
        except Exception:
            was_connected = redis_connected
            redis_connected = False
            if was_connected:
                if not partition_active:
                    partition_active = True
                    partition_mode = auto_partition_mode
                    partition_source = "auto"
                await broadcast_status()
            await asyncio.sleep(1)
        finally:
            if r is not None:
                try:
                    await r.aclose()
                except Exception:
                    pass


OUTBOX_POLL_INTERVAL = 0.1  # 100ms


async def outbox_publisher():
    while True:
        await asyncio.sleep(OUTBOX_POLL_INTERVAL)
        if partition_active:
            # Partition is active: drop accumulated outbox rows.
            # Simulated partitions resolve via the Heal button; infrastructure partitions
            # (Redis down) set partition_active=True via redis_subscriber, so this path
            # also covers that case. Writes to the local DB are preserved either way.
            async with AsyncSessionLocal() as session:
                await session.execute(text("DELETE FROM outbox"))
                await session.commit()
            continue
        if not redis_connected:
            continue
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    text("SELECT id, x, y, updated_at FROM outbox ORDER BY id LIMIT 100")
                )
                rows = result.fetchall()

            for row in rows:
                try:
                    r = aioredis.Redis(host=REDIS_HOST, port=REDIS_PORT, socket_connect_timeout=1)
                    payload = {
                        "source": "py",
                        "x": row.x,
                        "y": row.y,
                        "updated_at": row.updated_at.isoformat(),
                    }
                    await r.publish(REPLICATION_CHANNEL, json.dumps(payload))
                    redis_messages_published_total.inc()
                    await r.aclose()
                    async with AsyncSessionLocal() as del_session:
                        await del_session.execute(
                            text("DELETE FROM outbox WHERE id = :id"), {"id": row.id}
                        )
                        await del_session.commit()
                except Exception:
                    break  # Redis failed — leave row, retry next cycle
        except Exception:
            pass  # DB error — retry next cycle


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sub_task = asyncio.create_task(redis_subscriber())
    pub_task = asyncio.create_task(outbox_publisher())
    yield
    sub_task.cancel()
    pub_task.cancel()


app = FastAPI(title="backend-py", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://localhost(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)
Instrumentator().instrument(app).expose(app)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    t0 = time.monotonic()
    response = await call_next(request)
    if request.url.path != "/metrics":
        ms = round((time.monotonic() - t0) * 1000)
        _http_logger.info(
            "%s %s %d %dms", request.method, request.url.path, response.status_code, ms
        )
    return response


class PositionBody(BaseModel):
    x: float
    y: float


class PartitionBody(BaseModel):
    active: bool
    mode: str


class PartitionConfigBody(BaseModel):
    autoMode: str


@app.post("/admin/partition")
async def set_partition(body: PartitionBody):
    global partition_active, partition_mode, partition_source
    partition_active = body.active
    partition_mode = body.mode
    partition_source = "manual"
    return {"active": partition_active, "mode": partition_mode, "source": partition_source}


@app.post("/admin/partition-config")
async def set_partition_config(body: PartitionConfigBody):
    global auto_partition_mode
    auto_partition_mode = body.autoMode
    return {"autoMode": auto_partition_mode}


@app.get("/admin/status")
async def get_status():
    return {
        "partition": {"active": partition_active, "mode": partition_mode, "source": partition_source},
        "redis": {"connected": redis_connected},
    }


@app.get("/admin/local-state")
async def get_local_state():
    async with AsyncSessionLocal() as session:
        row = await session.get(PositionRow, DATA_ID)
        if row is None:
            return None
        return {"x": row.x, "y": row.y, "updated_at": row.updated_at.isoformat(), "source": "py"}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/position")
async def get_position():
    async with AsyncSessionLocal() as session:
        row = await session.get(PositionRow, DATA_ID)
        if row is None:
            return None
        return {"x": row.x, "y": row.y, "updated_at": row.updated_at.isoformat()}


@app.patch("/position")
async def update_position(body: PositionBody):
    if partition_active and partition_mode == "CP":
        raise HTTPException(status_code=503, detail="Partition active: CP mode rejects writes")
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        async with session.begin():
            await session.execute(
                text(
                    """
                    INSERT INTO positions (data_id, x, y, updated_at)
                    VALUES (:data_id, :x, :y, :updated_at)
                    ON CONFLICT (data_id) DO UPDATE
                    SET x = EXCLUDED.x, y = EXCLUDED.y, updated_at = EXCLUDED.updated_at
                    """
                ),
                {"data_id": DATA_ID, "x": body.x, "y": body.y, "updated_at": now},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO outbox (data_id, x, y, updated_at, created_at)
                    VALUES (:data_id, :x, :y, :updated_at, :created_at)
                    """
                ),
                {"data_id": DATA_ID, "x": body.x, "y": body.y, "updated_at": now, "created_at": now},
            )
            # session.begin() auto-commits on __aexit__

    payload = {"x": body.x, "y": body.y, "updated_at": now.isoformat()}
    await broadcast(payload)
    return payload


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    ws_connections_active.inc()
    try:
        await ws.send_text(json.dumps({
            "type": "status",
            "partition": {"active": partition_active, "mode": partition_mode, "source": partition_source},
            "redis": {"connected": redis_connected},
        }))
        while True:
            await ws.receive_text()  # keep connection alive
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)
        ws_connections_active.dec()
