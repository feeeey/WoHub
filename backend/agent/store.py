"""Persistence for agent runs & decisions."""
import json
from database import get_db
from config import settings


def _clean(sym: str) -> str:
    return sym.replace("BINANCE:", "").replace(".P", "")


def record_rule_run(task_id, decisions, signal_id_map) -> int | None:
    """Baseline: one 'rule' run per signal-producing execution. Written for
    every watchlist/market_scan run regardless of agent_decide action."""
    if not decisions:
        return None
    db = get_db(settings.db_path)
    cur = db.execute(
        "INSERT INTO agent_runs (task_id, decider, status, finished_at) VALUES (?, 'rule', 'done', datetime('now'))",
        (task_id,))
    run_id = cur.lastrowid
    for d in decisions:
        ids = signal_id_map.get((_clean(d.symbol), d.timeframe), [])
        db.execute(
            """INSERT INTO agent_decisions
               (run_id, signal_id, signal_ids_json, symbol, timeframe, direction, confidence, reasons, factors_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (run_id, ids[0] if ids else None, json.dumps(ids), _clean(d.symbol),
             d.timeframe, d.direction, d.confidence, d.reasons))
    db.commit()
    db.close()
    return run_id
