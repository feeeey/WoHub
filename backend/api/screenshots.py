from fastapi import APIRouter

from screenshots.client import chartshot_client

router = APIRouter(prefix="/screenshots")


@router.get("/status")
def chartshot_status():
    """Check ChartShot service health."""
    try:
        result = chartshot_client.health()
        return {"ok": True, "status": result.get("status", "unknown")}
    except Exception as e:
        return {"ok": False, "status": "unreachable", "error": str(e)}


@router.get("/cookies")
def get_chartshot_cookies():
    """Get ChartShot cookies status."""
    try:
        result = chartshot_client.get_cookies()
        if result.get("ok"):
            raw = result.get("cookies", "")
            has = bool(raw.strip())
            display = raw[:80] + "..." if len(raw) > 80 else raw
            return {"ok": True, "has_cookies": has, "cookies_display": display}
        return {"ok": False, "error": result.get("error", "")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.put("/cookies")
def update_chartshot_cookies(body: dict):
    """Update ChartShot cookies."""
    raw = body.get("cookies", "")
    if not raw:
        return {"ok": False, "error": "cookies string required"}
    try:
        result = chartshot_client.update_cookies(raw)
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/cookies/test")
def test_chartshot_cookies():
    """Test ChartShot cookies validity."""
    try:
        return chartshot_client.test_cookies()
    except Exception as e:
        return {"ok": False, "error": str(e)}
