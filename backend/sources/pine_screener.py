import os
import json
import requests
from pathlib import Path
from config import settings
from app_logger import log as applog

SCREENERS_DIR = Path(__file__).resolve().parent.parent / "screeners"

SCREENER_NAMES = {
    "oscillator/divergence": "顶底背离",
    "oscillator/overbought_zone": "超买",
    "oscillator/oversold_zone": "超卖",
    "oscillator/volatility_alert": "波动警报",
    "trend/shadows": "长上影/长下影",
    "trend/trend_volume_spike": "趋势爆量",
}

RESOLUTION_MAP = {
    "1m": "1", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "4h": "240", "1d": "1D", "1w": "1W",
}

VALID_RESOLUTIONS = set(RESOLUTION_MAP.keys())

API_URL = "https://pine-screener.tradingview.com/pine_scanner_http/scan"

_HEADERS = {
    "accept": "application/json",
    "accept-language": "zh-CN,zh;q=0.9,ts;q=0.8",
    "cache-control": "no-cache",
    "content-type": "text/plain;charset=UTF-8",
    "origin": "https://www.tradingview.com",
    "pragma": "no-cache",
    "priority": "u=1, i",
    "referer": "https://www.tradingview.com/",
    "sec-ch-ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
}

_DEFAULT_COOKIES = {
    "cookiePrivacyPreferenceBannerProduction": "notApplicable",
    "_ga": "GA1.1.988197279.1751463257",
    "cookiesSettings": '{"analytics":true,"advertising":true}',
    "device_t": "TEJXbkFROjI.yyWBVaicBcv2-vz8MOi8BXPXha69djS99xuip1BAK_4",
    "cachec": "undefined",
    "etg": "undefined",
    "theme": "light",
}

_session = None


def reset_session():
    global _session
    _session = None


def _get_session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(_HEADERS)
        _session.timeout = 15
        if settings.proxy_enabled:
            proxy = f"http://{settings.proxy_host}:{settings.proxy_port}"
            _session.proxies = {"http": proxy, "https": proxy}
    return _session


def _load_cookies():
    """Load cookies, merging saved values with defaults (matching original pine-screener behavior)."""
    merged = dict(_DEFAULT_COOKIES)
    cookie_path = Path(settings.db_path).parent / "cookies.json"
    if cookie_path.exists():
        try:
            with open(cookie_path) as f:
                saved = json.load(f)
            merged.update(saved)
        except Exception:
            pass
    return merged


def list_screeners():
    result = []
    for folder in ("oscillator", "trend"):
        folder_path = SCREENERS_DIR / folder
        if not folder_path.exists():
            continue
        for f in sorted(folder_path.glob("*.json")):
            name = f.stem
            key = f"{folder}/{name}"
            result.append({
                "folder_type": folder,
                "screener_name": name,
                "label": SCREENER_NAMES.get(key, name),
            })
    return result


def run_screener(folder_type, screener_name, resolution, watchlist_id):
    if folder_type not in ("oscillator", "trend"):
        raise ValueError(f"Invalid folder_type: {folder_type}")
    if resolution not in VALID_RESOLUTIONS:
        raise ValueError(f"Invalid resolution: {resolution}")

    config_path = SCREENERS_DIR / folder_type / f"{screener_name}.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Screener not found: {config_path}")

    with open(config_path) as f:
        config = json.load(f)

    config["scripts"][0]["resolution"] = RESOLUTION_MAP[resolution]
    config["watchlist"] = watchlist_id

    session = _get_session()
    cookies = _load_cookies()
    request_data = json.dumps(config)

    applog("pine_screener", "info",
           f"run_screener: {folder_type}/{screener_name} res={resolution} watchlist={watchlist_id}",
           f"cookie_keys={list(cookies.keys())}, header_count={len(session.headers)}")

    try:
        resp = session.post(API_URL, data=request_data, cookies=cookies)
        applog("pine_screener", "info",
               f"Response: HTTP {resp.status_code}, {len(resp.text)} bytes",
               f"response_headers={dict(resp.headers)}")
        resp.raise_for_status()
    except Exception as e:
        applog("pine_screener", "error", f"Request failed: {e}",
               f"url={API_URL}, status={getattr(resp, 'status_code', 'N/A') if 'resp' in dir() else 'N/A'}")
        raise

    symbols = []
    seen = set()
    raw_lines = resp.text.strip().split("\n")
    applog("pine_screener", "debug", f"Response lines: {len(raw_lines)}",
           f"first_100_chars={resp.text[:100]}")

    for line in raw_lines:
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            for sym in data.get("snapshot", {}).get("symbols", []):
                s = sym.get("s", "")
                if s and s not in seen:
                    seen.add(s)
                    symbols.append(s)
        except json.JSONDecodeError:
            applog("pine_screener", "warn", f"Failed to parse line", f"line={line[:200]}")
            continue

    applog("pine_screener", "info", f"Result: {len(symbols)} symbols found",
           f"symbols={symbols[:10]}{'...' if len(symbols) > 10 else ''}")
    return symbols


def fetch_watchlists():
    session = _get_session()
    cookies = _load_cookies()
    headers = {
        "accept": "*/*",
        "referer": "https://www.tradingview.com/pine-screener/",
        "x-language": "zh_CN",
        "x-requested-with": "XMLHttpRequest",
    }
    resp = session.get(
        "https://www.tradingview.com/api/v1/symbols_list/all/",
        headers=headers,
        cookies=cookies,
    )
    resp.raise_for_status()
    return {item["name"]: item["id"] for item in resp.json() if item.get("name")}


def build_cross_analysis(results):
    # Screener overlap: symbols in >=2 screeners
    symbol_screeners = {}
    for r in results:
        for sym in r.get("symbols", []):
            symbol_screeners.setdefault(sym, []).append(r["label"])
    screener_overlap = {
        sym: labels for sym, labels in symbol_screeners.items() if len(labels) >= 2
    }

    # Resolution overlap: per screener, symbols in >=2 resolutions
    screener_res = {}
    for r in results:
        label = r["label"]
        res = r.get("resolution", "")
        for sym in r.get("symbols", []):
            screener_res.setdefault(label, {}).setdefault(sym, []).append(res)
    resolution_overlap = {}
    for label, syms in screener_res.items():
        multi = {sym: ress for sym, ress in syms.items() if len(ress) >= 2}
        if multi:
            resolution_overlap[label] = multi

    # Full overlap: intersection of ALL result sets
    if results:
        sets = [set(r.get("symbols", [])) for r in results if r.get("symbols")]
        full = set.intersection(*sets) if sets else set()
    else:
        full = set()

    return {
        "screener_overlap": screener_overlap,
        "screener_overlap_count": len(screener_overlap),
        "resolution_overlap": resolution_overlap,
        "full_overlap": sorted(full),
        "full_overlap_count": len(full),
    }
