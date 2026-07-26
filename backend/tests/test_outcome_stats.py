import pytest

from agent import outcome_stats
from config import settings
from database import get_db


@pytest.fixture(autouse=True)
def _clear_cache():
    outcome_stats.clear_cache()
    yield
    outcome_stats.clear_cache()


def _seed(label, changes, hours_ago_start=48, legacy_encoding=False):
    """给 label 造 len(changes) 条信号+outcome，按时间递增排布。"""
    db = get_db(settings.db_path)
    try:
        for i, chg in enumerate(changes):
            indicator = f"{label}(1h)" if legacy_encoding else label
            cur = db.execute(
                "INSERT INTO signals (symbol, exchange, indicator, timeframe, triggered_at) "
                "VALUES ('BTCUSDT', 'Binance', ?, '1h', datetime('now', ?))",
                (indicator, f"-{hours_ago_start - i} hours"))
            if chg is not None:
                db.execute(
                    "INSERT INTO outcomes (signal_id, change_1h, change_4h, change_24h) "
                    "VALUES (?, ?, ?, ?)", (cur.lastrowid, chg, chg, chg))
        db.commit()
    finally:
        db.close()


def test_stats_aggregate_up_rate_and_avg():
    _seed("底背离", [1.0, 2.0, -1.0, 0.5, -0.5, 1.5])   # 4/6 上涨
    stats = outcome_stats.get_stats()
    e = stats["底背离"]
    assert e["n"] == 6
    assert e["4h"]["tracked"] == 6
    assert e["4h"]["up_rate"] == round(4 / 6, 4)
    assert e["4h"]["avg_change"] == round((1.0 + 2.0 - 1.0 + 0.5 - 0.5 + 1.5) / 6, 4)


def test_legacy_label_encoding_merged():
    """迁移前的 'label(res)' 编码要并进同一个 label 桶。"""
    _seed("超卖", [1.0, 1.0, 1.0])
    _seed("超卖", [-1.0, -1.0, -1.0], hours_ago_start=20, legacy_encoding=True)
    stats = outcome_stats.get_stats()
    assert stats["超卖"]["n"] == 6
    assert stats["超卖"]["4h"]["up_rate"] == 0.5


def test_window_excludes_old_signals():
    _seed("超买", [1.0] * 5)
    db = get_db(settings.db_path)
    try:
        cur = db.execute(
            "INSERT INTO signals (symbol, exchange, indicator, timeframe, triggered_at) "
            "VALUES ('BTCUSDT', 'Binance', '超买', '1h', datetime('now', '-200 days'))")
        db.execute("INSERT INTO outcomes (signal_id, change_4h) VALUES (?, -99.0)",
                   (cur.lastrowid,))
        db.commit()
    finally:
        db.close()
    stats = outcome_stats.get_stats(days=90)
    assert stats["超买"]["n"] == 5          # 200 天前的不计入


def test_small_sample_yields_null_stats():
    """n < MIN_SAMPLES：tracked 照实、统计值必须是 None——小样本占比不配当证据。"""
    _seed("长影线", [1.0, -1.0])
    stats = outcome_stats.get_stats()
    e = stats["长影线"]
    assert e["4h"]["tracked"] == 2
    assert e["4h"]["up_rate"] is None
    assert e["4h"]["avg_change"] is None


def test_untracked_signals_counted_in_n_only():
    _seed("放量", [1.0, 1.0, 1.0, 1.0, 1.0, None, None])
    stats = outcome_stats.get_stats()
    assert stats["放量"]["n"] == 7
    assert stats["放量"]["4h"]["tracked"] == 5


def test_cache_hit_and_clear():
    _seed("底背离", [1.0] * 5)
    first = outcome_stats.get_stats()
    _seed("底背离", [-1.0] * 5, hours_ago_start=10)
    assert outcome_stats.get_stats() is first          # TTL 内命中缓存
    outcome_stats.clear_cache()
    assert outcome_stats.get_stats()["底背离"]["n"] == 10


def test_format_stats_line():
    _seed("底背离", [1.0, 1.0, 1.0, -1.0, -1.0, 1.0])
    stats = outcome_stats.get_stats()
    line = outcome_stats.format_stats_line("底背离", stats)
    assert "n=6" in line and "上涨占比" in line and "4h" in line

    _seed("超卖", [1.0, -1.0])
    outcome_stats.clear_cache()
    line2 = outcome_stats.format_stats_line("超卖", outcome_stats.get_stats())
    assert "样本不足" in line2

    assert outcome_stats.format_stats_line("不存在的label", stats) is None
