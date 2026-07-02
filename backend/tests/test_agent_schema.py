import os
import sqlite3
import pytest


def _cols(table):
    db = sqlite3.connect(os.environ["DB_PATH"])
    rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    db.close()
    return {r[1] for r in rows}


def test_outcome_checks_table():
    assert {"id", "signal_id", "horizon", "due_at", "done", "error", "created_at"} <= _cols("outcome_checks")


def test_agent_runs_table():
    assert {"id", "task_id", "decider", "status", "context_json", "trace_json",
            "model", "prompt_version", "input_tokens", "output_tokens",
            "error", "created_at", "started_at", "finished_at"} <= _cols("agent_runs")


def test_agent_decisions_table():
    assert {"id", "run_id", "signal_id", "signal_ids_json", "symbol", "timeframe",
            "direction", "confidence", "reasons", "factors_json",
            "human_rating", "created_at"} <= _cols("agent_decisions")


def test_indexes_exist():
    db = sqlite3.connect(os.environ["DB_PATH"])
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    db.close()
    assert "idx_outcome_checks_due" in names
    assert "idx_agent_runs_status" in names
    assert "idx_agent_decisions_symbol" in names
