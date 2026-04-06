import os
import importlib.util
import httpx
from config import COOKIE_DIR, COOKIE_FILE


def _load_module(filename):
    filepath = os.path.join(COOKIE_DIR, filename)
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Cookie file not found: {filepath}")
    spec = importlib.util.spec_from_file_location("cookie_conf", filepath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_cookies(filename=None, domain=".tradingview.com"):
    mod = _load_module(filename or COOKIE_FILE)
    raw = getattr(mod, "cookies", None)
    if not raw or not isinstance(raw, dict):
        raise ValueError("No valid 'cookies' dict found in config")
    return [
        {"name": k, "value": v, "domain": domain, "path": "/"}
        for k, v in raw.items()
    ]


def load_headers(filename=None):
    mod = _load_module(filename or COOKIE_FILE)
    return getattr(mod, "headers", {})


def get_raw_cookie_string(filename=None):
    mod = _load_module(filename or COOKIE_FILE)
    raw = getattr(mod, "cookies", {})
    return "; ".join(f"{k}={v}" for k, v in raw.items())


def save_cookies_from_string(raw_cookie, filename=None):
    filename = filename or COOKIE_FILE
    filepath = os.path.join(COOKIE_DIR, filename)
    os.makedirs(COOKIE_DIR, exist_ok=True)

    pairs = {}
    for part in raw_cookie.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            pairs[k.strip()] = v.strip()

    headers = {}
    try:
        mod = _load_module(filename)
        headers = getattr(mod, "headers", {})
    except Exception:
        pass

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"cookies = {repr(pairs)}\n\n")
        f.write(f"headers = {repr(headers)}\n")


def test_validity(filename=None):
    try:
        cookie_str = get_raw_cookie_string(filename)
        resp = httpx.get(
            "https://cn.tradingview.com/",
            headers={"Cookie": cookie_str, "User-Agent": "Mozilla/5.0"},
            follow_redirects=True,
            timeout=10,
        )
        text = resp.text
        if '"username":"' in text:
            start = text.index('"username":"') + len('"username":"')
            end = text.index('"', start)
            return {"valid": True, "username": text[start:end]}
        if '"isLoggedIn":true' in text:
            return {"valid": True, "username": "unknown"}
        return {"valid": False, "error": "Not logged in"}
    except Exception as e:
        return {"valid": False, "error": str(e)}
