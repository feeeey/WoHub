import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from config import settings
from database import init_db
from api import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(settings.db_path)
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
