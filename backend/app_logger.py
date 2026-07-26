"""
Ring-buffer logger for debugging API requests.
Keeps recent log entries in memory, accessible via /api/settings/logs.

问题记录本会被日常流水冲掉：一次 run_screener 就写 4~5 条 info/debug，几个任务
跑一轮就是上百条，单一 200 条的环形缓冲区里，出错信息往往几分钟内就被挤没了 ——
而排障恰恰发生在事后。所以 warn/error 另存一条更长的问题缓冲区，查询时与流水
合并去重：调试噪声该被冲掉，故障证据不该。
"""
import itertools
import threading
from collections import deque
from datetime import datetime, timezone

STREAM_CAPACITY = 1000     # 全部级别的流水
PROBLEM_CAPACITY = 500     # 仅 warn/error，保留得久得多
PROBLEM_LEVELS = ("warn", "warning", "error", "critical")

_logs = deque(maxlen=STREAM_CAPACITY)
_problems = deque(maxlen=PROBLEM_CAPACITY)
_lock = threading.Lock()
_seq = itertools.count()   # 单调序号：合并两个缓冲区时用来去重 + 稳定排序


def log(source: str, level: str, message: str, detail: str = None):
    entry = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "level": level,
        "message": message,
        "detail": detail,
    }
    with _lock:
        entry["_seq"] = next(_seq)
        _logs.append(entry)
        if level in PROBLEM_LEVELS:
            _problems.append(entry)
    # Also print for Docker logs
    tag = f"[{source}]"
    print(f"{tag} {level.upper()}: {message}" + (f" | {detail[:500]}" if detail else ""))


def get_logs(source: str = None, level: str = None, limit: int = 100):
    with _lock:
        merged = {e["_seq"]: e for e in _logs}
        merged.update({e["_seq"]: e for e in _problems})
    entries = sorted(merged.values(), key=lambda e: e["_seq"], reverse=True)
    if source:
        entries = [e for e in entries if e["source"] == source]
    if level:
        entries = [e for e in entries if e["level"] == level]
    return [{k: v for k, v in e.items() if k != "_seq"} for e in entries[:limit]]


def clear_logs():
    with _lock:
        _logs.clear()
        _problems.clear()
