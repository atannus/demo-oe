from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from database import AsyncSessionLocal, DATA_ID, PositionRow
from schemas import PositionBody
from state import app_state

router = APIRouter()


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
