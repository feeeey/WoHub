"""
Ring-buffer logger for debugging API requests.
Keeps the last N log entries in memory, accessible via /api/settings/logs.
"""
import threading
import time
from collections import deque
from datetime import datetime, timezone

_logs = deque(maxlen=200)
_lock = threading.Lock()


def log(source: str, level: str, message: str, detail: str = None):
    entry = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "level": level,
        "message": message,
        "detail": detail,
    }
    with _lock:
        _logs.append(entry)
    # Also print for Docker logs
    tag = f"[{source}]"
    print(f"{tag} {level.upper()}: {message}" + (f" | {detail[:500]}" if detail else ""))


def get_logs(source: str = None, level: str = None, limit: int = 100):
    with _lock:
        entries = list(_logs)
    entries.reverse()  # newest first
    if source:
        entries = [e for e in entries if e["source"] == source]
    if level:
        entries = [e for e in entries if e["level"] == level]
    return entries[:limit]


def clear_logs():
    with _lock:
        _logs.clear()
