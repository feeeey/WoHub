from fastapi import APIRouter, Depends
from api.health import router as health_router
from auth import router as auth_router, require_auth
from api.market import router as market_router
from api.channels import router as channels_router
from api.tasks import router as tasks_router
from api.settings import router as settings_router
from api.scanner import router as scanner_router
from api.screenshots import router as screenshots_router
from api.klines import router as klines_router
from api.trading import router as trading_router
from api.agent import router as agent_router

api_router = APIRouter(prefix="/api")

# --- public (no auth required) ---
api_router.include_router(health_router)
api_router.include_router(auth_router)

# --- protected (cookie-session auth required) ---
protected = APIRouter(dependencies=[Depends(require_auth)])
protected.include_router(market_router)
protected.include_router(channels_router)
protected.include_router(tasks_router)
protected.include_router(settings_router)
protected.include_router(scanner_router)
protected.include_router(screenshots_router)
protected.include_router(klines_router)
protected.include_router(trading_router)
protected.include_router(agent_router)
api_router.include_router(protected)
