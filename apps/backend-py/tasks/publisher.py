import asyncio
import json

import redis.asyncio as aioredis
from sqlalchemy import text

from config import settings
from database import AsyncSessionLocal
from state import app_state, redis_messages_published_total
from tasks.subscriber import REPLICATION_CHANNEL

OUTBOX_POLL_INTERVAL = 0.1


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
