import os
import pytest
from database import get_db


def _seed_decision(decider, direction, confidence, change_4h):
    db = get_db(os.environ["DB_PATH"])
    cur = db.execute("INSERT INTO signals (task_id, symbol, exchange, indicator, timeframe) "
                     "VALUES (NULL, 'BTCUSDT', 'Binance', 'x', '1h')")
    sid = cur.lastrowid
    db.execute("INSERT INTO outcomes (signal_id, change_4h) VALUES (?, ?)", (sid, change_4h))
    cur = db.execute("INSERT INTO agent_runs (decider, status) VALUES (?, 'done')", (decider,))
    db.execute("INSERT INTO agent_decisions (run_id, signal_id, symbol, timeframe, direction, confidence, reasons) "
               "VALUES (?, ?, 'BTCUSDT', '1h', ?, ?, '')",
               (cur.lastrowid, sid, direction, confidence))
    db.commit()
    db.close()


@pytest.mark.asyncio
async def test_stats_direction_aware(client):
    _seed_decision("agent", "long", 0.8, +3.0)    # long×涨 = win
    _seed_decision("agent", "short", 0.8, +3.0)   # short×涨 = loss
    _seed_decision("agent", "skip", 0.2, +3.0)    # skip 不计
    _seed_decision("rule", "long", None, -1.0)    # rule 基线，loss
    async with client as c:
        body = (await c.get("/api/agent/stats")).json()
    agent_hi = next(g for g in body["groups"]
                    if g["decider"] == "agent" and g["bucket"] == ">=0.7" and g["horizon"] == "4h")
    assert agent_hi["n"] == 2 and agent_hi["wins"] == 1 and abs(agent_hi["win_rate"] - 0.5) < 1e-6
    assert agent_hi["reliable"] is False          # n < 20
    rule = next(g for g in body["groups"] if g["decider"] == "rule" and g["horizon"] == "4h")
    assert rule["n"] == 1 and rule["wins"] == 0
