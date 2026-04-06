from fastapi import APIRouter
from database import get_db
from config import settings

router = APIRouter()


@router.get("/health")
def health_check():
    db_status = "disconnected"
    try:
        conn = get_db(settings.db_path)
        conn.execute("SELECT 1")
        conn.close()
        db_status = "connected"
    except Exception:
        pass

    return {
        "status": "ok",
        "version": "0.1.0",
        "database": db_status,
    }
