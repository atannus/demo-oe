import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import text

from database import AsyncSessionLocal, DATA_ID, PositionRow
from state import app_state, ws_connections_active

router = APIRouter()


class PositionBody(BaseModel):
    x: float
    y: float


class PartitionBody(BaseModel):
    active: bool
    mode: str


class PartitionConfigBody(BaseModel):
    autoMode: str


@router.post("/admin/partition")
async def set_partition(body: PartitionBody) -> dict:
    app_state.partition_active = body.active
    app_state.partition_mode = body.mode
    app_state.partition_source = "manual"
    return {
        "active": app_state.partition_active,
        "mode": app_state.partition_mode,
        "source": app_state.partition_source,
    }


@router.post("/admin/partition-config")
async def set_partition_config(body: PartitionConfigBody) -> dict:
    app_state.auto_partition_mode = body.autoMode
    return {"autoMode": app_state.auto_partition_mode}


@router.get("/admin/status")
async def get_status() -> dict:
    return {
        "partition": {
            "active": app_state.partition_active,
            "mode": app_state.partition_mode,
            "source": app_state.partition_source,
        },
        "redis": {"connected": app_state.redis_connected},
    }


@router.get("/admin/local-state")
async def get_local_state():
    async with AsyncSessionLocal() as session:
        row = await session.get(PositionRow, DATA_ID)
        if row is None:
            return None
        return {"x": row.x, "y": row.y, "updated_at": row.updated_at.isoformat(), "source": "py"}


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/position")
async def get_position():
    async with AsyncSessionLocal() as session:
        row = await session.get(PositionRow, DATA_ID)
        if row is None:
            return None
        return {"x": row.x, "y": row.y, "updated_at": row.updated_at.isoformat()}


@router.patch("/position")
async def update_position(body: PositionBody) -> dict:
    if app_state.partition_active and app_state.partition_mode == "CP":
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

    payload = {"x": body.x, "y": body.y, "updated_at": now.isoformat()}
    await app_state.broadcast(payload)
    return payload


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    app_state.clients.add(ws)
    ws_connections_active.inc()
    try:
        await ws.send_text(json.dumps({
            "type": "status",
            "partition": {
                "active": app_state.partition_active,
                "mode": app_state.partition_mode,
                "source": app_state.partition_source,
            },
            "redis": {"connected": app_state.redis_connected},
        }))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        app_state.clients.discard(ws)
        ws_connections_active.dec()
