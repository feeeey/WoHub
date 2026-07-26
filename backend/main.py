import os
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from config import settings
from database import init_db
from api import api_router


def _insecure_default_warning(bad: list[str]) -> str | None:
    """Build the startup security-warning message, or None if config is safe."""
    if not bad:
        return None
    msg = ("不安全的默认配置：" + ", ".join(bad) +
           " 仍为默认值；主网交易已被禁用。设置强随机值后重启。")
    if "SECRET_KEY" in bad:
        msg += ("（会话已改用 data/session_key 中自动生成的密钥签名，"
                "cookie 不可伪造；但已存 API secret 的加密仍派生自 SECRET_KEY，"
                "强度不足。）")
    return msg


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
    from tasks.outcome_poller import start_poller, stop_poller
    start_poller()
    from agent.chat.worker import start_worker, stop_worker
    start_worker()
    yield
    stop_worker()
    stop_poller()
    from tasks.scheduler import stop_scheduler
    stop_scheduler()


def resolve_static_file(static_dir: str, path: str) -> str | None:
    """Map a request path to a real file inside `static_dir`, or None.

    The SPA catch-all receives the path already percent-decoded, so a request
    for `/%2e%2e%2fdata%2fwohub.db` arrives here as `../data/wohub.db` —
    os.path.join would resolve that outside the static root and serve it.
    Resolve symlinks first, then require the result to stay under the root
    (same containment check as api/screenshots.py).
    """
    root = os.path.realpath(static_dir)
    candidate = os.path.realpath(os.path.join(root, path))
    if candidate != root and not candidate.startswith(root + os.sep):
        return None
    return candidate if os.path.isfile(candidate) else None


def mount_spa(app: FastAPI, static_dir: str) -> None:
    """Serve the built Vue app. Registered only when a build is present, which
    is why this lives in a function: tests can mount it on a throwaway app with
    a temp directory instead of needing a real production build."""
    app.mount("/assets", StaticFiles(directory=os.path.join(static_dir, "assets")),
              name="assets")

    @app.get("/{path:path}")
    async def serve_spa(path: str):
        # Unmatched /api/* must 404 rather than fall through to index.html —
        # the frontend parses every /api response as JSON.
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        file_path = resolve_static_file(static_dir, path)
        if file_path:
            return FileResponse(file_path)
        return FileResponse(os.path.join(static_dir, "index.html"))


app = FastAPI(title="WoHub", lifespan=lifespan)
app.include_router(api_router)

# Serve Vue frontend in production
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    mount_spa(app, _static_dir)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
