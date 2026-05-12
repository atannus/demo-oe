import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from database import Base, engine
from routers import router
from tasks import outbox_publisher, redis_subscriber

logging.getLogger("uvicorn.access").disabled = True

_http_logger = logging.getLogger("http")
_http_logger.setLevel(logging.INFO)
_http_handler = logging.StreamHandler()
_http_handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
_http_logger.addHandler(_http_handler)
_http_logger.propagate = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    tasks = [
        asyncio.create_task(redis_subscriber()),
        asyncio.create_task(outbox_publisher()),
    ]
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="backend-py", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://localhost(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)
Instrumentator().instrument(app).expose(app)
app.include_router(router)


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
