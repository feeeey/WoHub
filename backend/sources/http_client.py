import threading
import time
import requests
from config import settings

_session = None
_session_lock = threading.Lock()
_direct_session = None
_direct_lock = threading.Lock()

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
        s.headers.update({"User-Agent": "WoHub/0.1"})
        # Always opt out of HTTP(S)_PROXY env vars — if proxy_enabled is set
        # we use the explicit project config; if it's off, we want a direct
        # connection. Inheriting an unrelated env-level proxy (e.g. an IDE's
        # built-in proxy) silently misroutes requests.
        s.trust_env = False
        if settings.proxy_enabled:
            proxy_url = f"http://{settings.proxy_host}:{settings.proxy_port}"
            s.proxies = {"http": proxy_url, "https": proxy_url}
        _session = s
        return _session


def _get_direct_session() -> requests.Session:
    """Session without proxy for fallback."""
    global _direct_session
    if _direct_session is not None:
        return _direct_session
    with _direct_lock:
        if _direct_session is not None:
            return _direct_session
        s = requests.Session()
        s.headers.update({"User-Agent": "WoHub/0.1"})
        s.trust_env = False
        _direct_session = s
        return _direct_session


def fetch_with_fallback(method, url, **kwargs):
    """Try with proxy session first. If proxy fails, retry with direct connection.

    requests.Session has no timeout attribute, so a per-request timeout is
    injected here — otherwise a hung connection blocks the worker forever.
    Callers may override with an explicit timeout= kwarg.
    """
    kwargs.setdefault("timeout", 10)
    session = get_session()
    try:
        resp = getattr(session, method)(url, **kwargs)
        resp.raise_for_status()
        return resp
    except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError) as e:
        if not settings.proxy_enabled:
            raise
        print(f"[http] Proxy failed for {url}, falling back to direct: {e}")
        direct = _get_direct_session()
        resp = getattr(direct, method)(url, **kwargs)
        resp.raise_for_status()
        return resp


def reset_session():
    """Reset HTTP sessions so they pick up new proxy settings."""
    global _session, _direct_session
    with _session_lock:
        _session = None
    with _direct_lock:
        _direct_session = None


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
