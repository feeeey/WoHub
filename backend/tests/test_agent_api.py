import os
import pytest
from database import get_db


def _seed():
    db = get_db(os.environ["DB_PATH"])
    cur = db.execute("INSERT INTO agent_runs (task_id, decider, status, context_json, trace_json) "
                     "VALUES (NULL, 'agent', 'done', '{}', '{\"steps\":[]}')")
    rid = cur.lastrowid
    db.execute("INSERT INTO agent_decisions (run_id, symbol, timeframe, direction, confidence, reasons) "
               "VALUES (?, 'BTCUSDT', '1h', 'long', 0.7, 'r')", (rid,))
    db.commit()
    did = db.execute("SELECT id FROM agent_decisions WHERE run_id=?", (rid,)).fetchone()["id"]
    db.close()
    return rid, did


@pytest.mark.asyncio
async def test_list_and_detail(client):
    rid, _ = _seed()
    async with client as c:
        lst = (await c.get("/api/agent/runs")).json()
        det = (await c.get(f"/api/agent/runs/{rid}")).json()
    assert lst[0]["id"] == rid and lst[0]["decision_count"] == 1
    assert det["decisions"][0]["symbol"] == "BTCUSDT"
    assert det["trace"]["steps"] == []


@pytest.mark.asyncio
async def test_rate_decision(client):
    _, did = _seed()
    async with client as c:
        r = await c.post(f"/api/agent/decisions/{did}/rate", json={"rating": 1})
    assert r.status_code == 200
    db = get_db(os.environ["DB_PATH"])
    assert db.execute("SELECT human_rating FROM agent_decisions WHERE id=?", (did,)).fetchone()[0] == 1
    db.close()


@pytest.mark.asyncio
async def test_rerun_requeues(client):
    rid, _ = _seed()
    async with client as c:
        r = await c.post(f"/api/agent/runs/{rid}/rerun")
    new_id = r.json()["id"]
    db = get_db(os.environ["DB_PATH"])
    assert db.execute("SELECT status FROM agent_runs WHERE id=?", (new_id,)).fetchone()[0] == "queued"
    db.close()
