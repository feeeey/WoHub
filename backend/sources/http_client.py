import threading
import time
import requests
from config import settings

_session = None
_session_lock = threading.Lock()

_cache = {}
_cache_lock = threading.Lock()


def get_session() -> requests.Session:
    global _session
    if _session is not None:
        return _session
    with _session_lock:
        if _session is not None:
            return _session
        s = requests.Session()
        s.timeout = 10
        s.headers.update({"User-Agent": "WoHub/0.1"})
        if settings.proxy_enabled:
            proxy_url = f"http://{settings.proxy_host}:{settings.proxy_port}"
            s.proxies = {"http": proxy_url, "https": proxy_url}
        _session = s
        return _session


def reset_session():
    """Reset the HTTP session so it picks up new proxy settings."""
    global _session
    with _session_lock:
        _session = None


def cached(key: str, fetcher, ttl: float = None):
    if ttl is None:
        ttl = settings.cache_ttl
    now = time.time()
    with _cache_lock:
        entry = _cache.get(key)
        if entry and now - entry["ts"] < ttl:
            return entry["data"], entry["errors"]
    data, errors = fetcher()
    with _cache_lock:
        _cache[key] = {"data": data, "errors": errors, "ts": time.time()}
    return data, errors
