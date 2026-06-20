import os
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from config import settings
from database import init_db
from api import api_router


def _insecure_default_warning(bad: list[str]) -> str | None:
    """Build the startup security-warning message, or None if config is safe."""
    if not bad:
        return None
    return ("不安全的默认配置：" + ", ".join(bad) +
            " 仍为默认值；主网交易已被禁用。设置强随机值后重启。")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(settings.db_path)
    _msg = _insecure_default_warning(settings.insecure_defaults())
    if _msg:
        from app_logger import log as _applog
        _applog("security", "warn", _msg)
        _bar = "=" * 60
        print(f"\n{_bar}\n⚠️  WoHub: {_msg}\n{_bar}\n", file=sys.stderr, flush=True)
    from api.tasks import start_all_enabled
    start_all_enabled()
    yield
    from tasks.scheduler import stop_scheduler
    stop_scheduler()


app = FastAPI(title="WoHub", lifespan=lifespan)
app.include_router(api_router)

# Serve Vue frontend in production
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/assets", StaticFiles(directory=os.path.join(_static_dir, "assets")), name="assets")

    @app.get("/{path:path}")
    async def serve_spa(path: str):
        file_path = os.path.join(_static_dir, path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(_static_dir, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
