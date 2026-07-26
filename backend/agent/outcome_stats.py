"""筛选器信号的后验统计聚合 —— agent 闭环的数据侧。

signals×outcomes 按筛选器 label 聚合 1h/4h/24h 的真实后验收益。
统计是方向盲的（up_rate 是原始上涨占比，不是按信号方向交易的胜率）：
做空类信号应关注下跌占比。方向解释交给调用方（prompt 里的语义档案 /
OutcomeValidator 的 bias 声明），这里只供事实。

被两处消费：
- prompts._semantics_block：注入 system prompt，让 agent 的方向性论断可引用后验数据
- tools.screener_outcome_stats：作为工具暴露，回答「哪个筛选器最近靠谱」
"""
import threading
import time
from statistics import mean

from config import settings
from database import get_db

HORIZONS = ("1h", "4h", "24h")
DEFAULT_WINDOW_DAYS = 90
MIN_SAMPLES = 5          # 样本低于此数不给统计值，防止 n=2 的占比被当成证据
_CACHE_TTL = 600         # prompt 每轮都会构建，聚合查询不必每次都跑

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "days": None, "data": None}


def clear_cache() -> None:
    with _lock:
        _cache.update(at=0.0, days=None, data=None)


def get_stats(days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """{label: {n, per-horizon {tracked, up_rate, avg_change}}}，窗口内按 label 聚合。

    up_rate/avg_change 在样本 < MIN_SAMPLES 时为 None（tracked 照实返回）——
    宁可不给数字，也不让小样本占比被引用成证据。
    """
    days = max(7, min(int(days), 365))
    with _lock:
        if _cache["days"] == days and time.monotonic() - _cache["at"] < _CACHE_TTL:
            return _cache["data"]

    db = get_db(settings.db_path)
    try:
        # label 兼容迁移前的 'label(res)' 双编码旧数据（同 signal_history）
        rows = db.execute(
            """SELECT CASE WHEN instr(s.indicator, '(') > 0
                           THEN substr(s.indicator, 1, instr(s.indicator, '(') - 1)
                           ELSE s.indicator END AS label,
                      o.change_1h, o.change_4h, o.change_24h
               FROM signals s LEFT JOIN outcomes o ON o.signal_id = s.id
               WHERE s.triggered_at >= datetime('now', ?)""",
            (f"-{days} days",)).fetchall()
    finally:
        db.close()

    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["label"], []).append(r)

    data = {}
    for label, sigs in grouped.items():
        entry = {"n": len(sigs)}
        for h in HORIZONS:
            vals = [r[f"change_{h}"] for r in sigs if r[f"change_{h}"] is not None]
            stat = {"tracked": len(vals), "up_rate": None, "avg_change": None}
            if len(vals) >= MIN_SAMPLES:
                stat["up_rate"] = round(sum(1 for v in vals if v > 0) / len(vals), 4)
                stat["avg_change"] = round(mean(vals), 4)
            entry[h] = stat
        data[label] = entry

    with _lock:
        _cache.update(at=time.monotonic(), days=days, data=data)
    return data


def format_stats_line(label: str, stats: dict) -> str | None:
    """单个 label 的紧凑中文摘要（注入 prompt 用），无可用统计返回 None。"""
    entry = stats.get(label)
    if not entry:
        return None
    usable = [h for h in HORIZONS if entry[h]["up_rate"] is not None]
    if not usable:
        # 有信号但全部样本不足：明说，防止模型自行脑补统计
        return f"近{DEFAULT_WINDOW_DAYS}天后验：样本不足（n={entry['n']}），无统计意义" \
            if entry["n"] else None
    ups = " · ".join(f"{h} {entry[h]['up_rate'] * 100:.1f}%" for h in usable)
    avgs = " · ".join(f"{entry[h]['avg_change']:+.2f}%" for h in usable)
    return (f"近{DEFAULT_WINDOW_DAYS}天后验（n={entry['n']}）："
            f"上涨占比 {ups}；平均涨跌 {avgs}")
