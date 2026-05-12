from fastapi import APIRouter

from routers.admin import router as admin_router
from routers.position import router as position_router
from routers.ws import router as ws_router

router = APIRouter()
router.include_router(admin_router)
router.include_router(position_router)
router.include_router(ws_router)
