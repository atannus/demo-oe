import asyncio
import json
from datetime import datetime

import redis.asyncio as aioredis
from sqlalchemy import text

from config import settings
from database import AsyncSessionLocal, DATA_ID
from state import app_state, redis_messages_published_total, redis_messages_received_total

REPLICATION_CHANNEL = "position:replicated"
OUTBOX_POLL_INTERVAL = 0.1


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
    await app_state.broadcast({"x": x, "y": y, "updated_at": updated_at_str})


async def redis_subscriber() -> None:
    while True:
        r = None
        try:
            r = aioredis.Redis(host=settings.redis_host, port=settings.redis_port, socket_connect_timeout=2)
            pubsub = r.pubsub()
            await pubsub.subscribe(REPLICATION_CHANNEL)

            was_disconnected = not app_state.redis_connected
            app_state.redis_connected = True
            if was_disconnected:
                if app_state.partition_source == "auto":
                    app_state.partition_active = False
                    app_state.partition_source = "manual"
                await app_state.broadcast_status()

            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                if app_state.partition_active:
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
            was_connected = app_state.redis_connected
            app_state.redis_connected = False
            if was_connected:
                if not app_state.partition_active:
                    app_state.partition_active = True
                    app_state.partition_mode = app_state.auto_partition_mode
                    app_state.partition_source = "auto"
                await app_state.broadcast_status()
            await asyncio.sleep(1)
        finally:
            if r is not None:
                try:
                    await r.aclose()
                except Exception:
                    pass


async def outbox_publisher() -> None:
    while True:
        await asyncio.sleep(OUTBOX_POLL_INTERVAL)
        if app_state.partition_active:
            async with AsyncSessionLocal() as session:
                await session.execute(text("DELETE FROM outbox"))
                await session.commit()
            continue
        if not app_state.redis_connected:
            continue
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    text("SELECT id, x, y, updated_at FROM outbox ORDER BY id LIMIT 100")
                )
                rows = result.fetchall()

            for row in rows:
                try:
                    r = aioredis.Redis(host=settings.redis_host, port=settings.redis_port, socket_connect_timeout=1)
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
