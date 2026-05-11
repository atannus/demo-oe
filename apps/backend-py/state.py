import json
from dataclasses import dataclass, field

from fastapi import WebSocket
from prometheus_client import Counter, Gauge

ws_connections_active = Gauge("ws_connections_active", "Active WebSocket connections")
redis_messages_published_total = Counter("redis_messages_published_total", "Redis messages published")
redis_messages_received_total = Counter("redis_messages_received_total", "Redis messages received")


@dataclass
class AppState:
    clients: set[WebSocket] = field(default_factory=set)
    partition_active: bool = False
    partition_mode: str = "AP"
    partition_source: str = "manual"
    auto_partition_mode: str = "AP"
    redis_connected: bool = True

    async def broadcast(self, payload: dict) -> None:
        text_data = json.dumps(payload)
        dead: set[WebSocket] = set()
        for ws in self.clients:
            try:
                await ws.send_text(text_data)
            except Exception:
                dead.add(ws)
        self.clients.difference_update(dead)

    async def broadcast_status(self) -> None:
        await self.broadcast({
            "type": "status",
            "partition": {
                "active": self.partition_active,
                "mode": self.partition_mode,
                "source": self.partition_source,
            },
            "redis": {"connected": self.redis_connected},
        })


app_state = AppState()
