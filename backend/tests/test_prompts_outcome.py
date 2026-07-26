"""闭环注入测试：语义档案在有后验数据时带统计行，没数据时不带也不崩。"""
import pytest

from agent import outcome_stats
from agent.chat.prompts import build_system_prompt
from agent.chat.semantics import seed_defaults
from config import settings
from database import get_db
from sources.pine_screener import SCREENER_NAMES


@pytest.fixture(autouse=True)
def _clear_cache():
    outcome_stats.clear_cache()
    yield
    outcome_stats.clear_cache()


def _seed_outcomes(label, changes):
    db = get_db(settings.db_path)
    try:
        for i, chg in enumerate(changes):
            cur = db.execute(
                "INSERT INTO signals (symbol, exchange, indicator, timeframe, triggered_at) "
                "VALUES ('BTCUSDT', 'Binance', ?, '1h', datetime('now', ?))",
                (label, f"-{48 - i} hours"))
            db.execute("INSERT INTO outcomes (signal_id, change_1h, change_4h, change_24h) "
                       "VALUES (?, ?, ?, ?)", (cur.lastrowid, chg, chg, chg))
        db.commit()
    finally:
        db.close()


def test_prompt_includes_posterior_stats_when_data_exists():
    seed_defaults()
    label = SCREENER_NAMES["oscillator/divergence_bottom"]
    _seed_outcomes(label, [1.0, 2.0, -1.0, 0.5, -0.5, 1.5])

    prompt = build_system_prompt()
    assert "后验（n=6）" in prompt
    assert "上涨占比" in prompt
    assert "方向盲" in prompt          # 统计的解释性声明必须在场


def test_prompt_survives_empty_outcome_data():
    seed_defaults()
    prompt = build_system_prompt()
    assert "筛选器语义档案" in prompt   # 档案还在
    assert "后验（n=" not in prompt     # 没数据就没有统计行


def test_prompt_marks_insufficient_samples():
    seed_defaults()
    label = SCREENER_NAMES["oscillator/oversold_zone"]
    _seed_outcomes(label, [1.0, -1.0])   # n=2 < MIN_SAMPLES
    prompt = build_system_prompt()
    assert "样本不足" in prompt


def test_screener_stats_tool():
    from agent import tools as T
    _seed_outcomes("超卖", [1.0] * 4 + [-1.0] * 2)
    out = T.screener_outcome_stats(days=90)
    assert out["window_days"] == 90
    assert out["stats"]["超卖"]["4h"]["up_rate"] == round(4 / 6, 4)
    assert "方向盲" in out["note"]
    assert "error" in T.screener_outcome_stats(days="abc")
