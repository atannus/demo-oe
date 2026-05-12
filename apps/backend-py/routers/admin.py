from fastapi import APIRouter

from database import AsyncSessionLocal, DATA_ID, PositionRow
from schemas import PartitionBody, PartitionConfigBody
from state import app_state

router = APIRouter()


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
