from contextlib import asynccontextmanager
from fastapi import FastAPI
from config import settings
from database import init_db
from api import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(settings.db_path)
    yield


app = FastAPI(title="WoHub", lifespan=lifespan)
app.include_router(api_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
