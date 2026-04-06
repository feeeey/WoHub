from fastapi import APIRouter
from api.health import router as health_router
from auth import router as auth_router
from api.market import router as market_router
from api.channels import router as channels_router
from api.tasks import router as tasks_router

api_router = APIRouter(prefix="/api")
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(market_router)
api_router.include_router(channels_router)
api_router.include_router(tasks_router)
