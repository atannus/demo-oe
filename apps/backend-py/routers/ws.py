import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from state import app_state, ws_connections_active

router = APIRouter()


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
