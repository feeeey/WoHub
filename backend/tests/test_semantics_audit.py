import pytest

from agent.chat.semantics import seed_defaults
from config import settings
from database import get_db
from sources.pine_screener import SCREENER_NAMES


def _seed(label, changes):
    db = get_db(settings.db_path)
    try:
        step = 400 / max(len(changes), 1)
        for i, chg in enumerate(changes):
            cur = db.execute(
                "INSERT INTO signals (symbol, exchange, indicator, timeframe, triggered_at) "
                "VALUES ('BTCUSDT', 'Binance', ?, '1h', datetime('now', ?))",
                (label, f"-{400 - i * step:.0f} hours"))
            db.execute("INSERT INTO outcomes (signal_id, change_4h) VALUES (?, ?)",
                       (cur.lastrowid, chg))
        db.commit()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_audit_covers_all_profiles_with_correct_verdict_types(client):
    seed_defaults()
    resp = await client.post("/api/agent/semantics/validate")
    assert resp.status_code == 200
    data = resp.json()
    results = {r["key"]: r for r in data["results"]}

    # 双向/中性档案：如实 skipped，不硬造裁决
    assert results["trend/shadows"]["verdict"] == "skipped"
    assert results["oscillator/volatility_alert"]["verdict"] == "skipped"
    # 有方向声明但空库：样本不足 → not_validated
    assert results["oscillator/divergence_bottom"]["verdict"] == "not_validated"
    assert "Bonferroni" in data["note"]


@pytest.mark.asyncio
async def test_audit_passes_strong_signal(client):
    seed_defaults()
    label = SCREENER_NAMES["oscillator/divergence_bottom"]
    _seed(label, [1.0, 1.0, 1.0, -1.0] * 20)      # 75% 上涨、各折一致、n=80

    resp = await client.post("/api/agent/semantics/validate")
    r = next(x for x in resp.json()["results"]
             if x["key"] == "oscillator/divergence_bottom")
    assert r["verdict"] == "pass"
    assert r["n"] == 80 and r["hit_rate"] == 0.75
    # 一次审计多条检验：alpha 必须被校正到 < 0.05
    assert r["alpha_adjusted"] < 0.05


@pytest.mark.asyncio
async def test_audit_fails_contradicted_claim(client):
    """声称偏多但后验偏空 —— 审计必须给 fail，这正是它存在的意义。"""
    seed_defaults()
    label = SCREENER_NAMES["oscillator/oversold_zone"]
    _seed(label, [-1.0, -1.0, 1.0] * 20)          # 只有 1/3 上涨

    resp = await client.post("/api/agent/semantics/validate")
    r = next(x for x in resp.json()["results"]
             if x["key"] == "oscillator/oversold_zone")
    assert r["verdict"] == "fail"
