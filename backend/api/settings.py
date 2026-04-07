import json
import os
from pathlib import Path
from fastapi import APIRouter
from config import settings
from sources.chart_shot_client import chartshot_client
from app_logger import get_logs, clear_logs

router = APIRouter(prefix="/settings")


def _cookies_path():
    return Path(settings.db_path).parent / "cookies.json"


@router.get("/cookies")
def get_cookies():
    """Get current TradingView cookies (used by Pine screener)."""
    path = _cookies_path()
    if path.exists():
        with open(path) as f:
            cookies = json.load(f)
        # Mask sensitive values for display
        display = "; ".join(f"{k}={v[:8]}..." if len(v) > 12 else f"{k}={v}" for k, v in cookies.items())
        return {"ok": True, "cookies_display": display, "has_cookies": bool(cookies)}
    return {"ok": True, "cookies_display": "", "has_cookies": False}


@router.put("/cookies")
def update_cookies(body: dict):
    """Update TradingView cookies from raw string."""
    raw = body.get("cookies", "")
    if not raw:
        return {"ok": False, "error": "cookies string required"}

    # Parse cookie string: "k1=v1; k2=v2; ..." → {k1: v1, k2: v2}
    pairs = {}
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            pairs[k.strip()] = v.strip()

    if not pairs:
        return {"ok": False, "error": "No valid cookies parsed"}

    path = _cookies_path()
    os.makedirs(path.parent, exist_ok=True)
    with open(path, "w") as f:
        json.dump(pairs, f, indent=2)

    return {"ok": True, "count": len(pairs)}


@router.get("/chartshot/status")
def chartshot_status():
    """Check ChartShot service health."""
    try:
        result = chartshot_client.health()
        return {"ok": True, "status": result.get("status", "unknown")}
    except Exception as e:
        return {"ok": False, "status": "unreachable", "error": str(e)}


@router.get("/chartshot/cookies")
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


@router.put("/chartshot/cookies")
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


@router.post("/chartshot/cookies/test")
def test_chartshot_cookies():
    """Test ChartShot cookies validity."""
    try:
        return chartshot_client.test_cookies()
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/info")
def get_info():
    """Get system info and current settings."""
    return {
        "version": "0.1.0",
        "cache_ttl": settings.cache_ttl,
        "min_volume_24h": settings.min_volume_24h,
        "proxy_enabled": settings.proxy_enabled,
        "proxy_host": settings.proxy_host,
        "proxy_port": settings.proxy_port,
        "chartshot_url": settings.chartshot_url,
    }


@router.get("/logs")
def api_logs(source: str = None, level: str = None, limit: int = 100):
    return get_logs(source=source, level=level, limit=limit)


@router.delete("/logs")
def api_clear_logs():
    clear_logs()
    return {"ok": True}


@router.get("/proxy")
def get_proxy():
    """Get proxy configuration."""
    return {
        "enabled": settings.proxy_enabled,
        "host": settings.proxy_host,
        "port": settings.proxy_port,
        "scope": [
            "交易所 API（Binance, OKX, Bybit, Bitget）",
            "TradingView Pine 筛选 API",
            "AI API（如使用海外服务）",
        ],
    }


@router.put("/proxy")
def update_proxy(body: dict):
    """Update proxy configuration. Takes effect after restart or session reset."""
    enabled = body.get("enabled")
    host = body.get("host", "").strip()
    port = body.get("port", "").strip()

    if enabled is not None:
        settings.proxy_enabled = bool(enabled)
    if host:
        settings.proxy_host = host
    if port:
        settings.proxy_port = str(port)

    # Reset HTTP sessions so they pick up new proxy
    from sources.http_client import reset_session
    reset_session()

    from sources.pine_screener import reset_session as reset_pine_session
    reset_pine_session()

    return {"ok": True, "enabled": settings.proxy_enabled, "host": settings.proxy_host, "port": settings.proxy_port}
