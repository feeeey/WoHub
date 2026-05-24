from fastapi import APIRouter
from api.health import router as health_router
from auth import router as auth_router
from api.market import router as market_router
from api.channels import router as channels_router
from api.tasks import router as tasks_router
from api.settings import router as settings_router
from api.ai import router as ai_router
from api.scanner import router as scanner_router
from api.screenshots import router as screenshots_router
from api.klines import router as klines_router

api_router = APIRouter(prefix="/api")
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(market_router)
api_router.include_router(channels_router)
api_router.include_router(tasks_router)
api_router.include_router(settings_router)
api_router.include_router(ai_router)
api_router.include_router(scanner_router)
api_router.include_router(screenshots_router)
api_router.include_router(klines_router)
