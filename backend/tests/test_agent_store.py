import os, json
from database import get_db
from agent.decider import Decision
from agent.store import record_rule_run


def _seed_signals(n=2):
    """FK 约束开启（get_db 设 PRAGMA foreign_keys=ON），signal_id 必须真实存在。"""
    db = get_db(os.environ["DB_PATH"])
    ids = []
    for _ in range(n):
        cur = db.execute("INSERT INTO signals (task_id, symbol, exchange, indicator, timeframe) "
                         "VALUES (NULL, 'AUSDT', 'Binance', '底背离', '1h')")
        ids.append(cur.lastrowid)
    db.commit()
    db.close()
    return ids


def test_record_rule_run_writes_run_and_decisions():
    sid1, sid2 = _seed_signals(2)
    decisions = [Decision(symbol="BINANCE:AUSDT.P", timeframe="1h", direction="long",
                          confidence=None, reasons="规则：2 个筛选器命中（阈值 2）", labels=["底背离", "超卖"])]
    id_map = {("AUSDT", "1h"): [sid1, sid2]}
    run_id = record_rule_run(task_id=None, decisions=decisions, signal_id_map=id_map)
    db = get_db(os.environ["DB_PATH"])
    run = db.execute("SELECT decider, status FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
    d = db.execute("SELECT * FROM agent_decisions WHERE run_id = ?", (run_id,)).fetchone()
    db.close()
    assert (run["decider"], run["status"]) == ("rule", "done")
    assert (d["symbol"], d["timeframe"], d["direction"], d["signal_id"]) == ("AUSDT", "1h", "long", sid1)
    assert json.loads(d["signal_ids_json"]) == [sid1, sid2]


def test_record_rule_run_empty_is_noop():
    assert record_rule_run(task_id=None, decisions=[], signal_id_map={}) is None
