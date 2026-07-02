import sqlite3
import tempfile
import os

from database import init_db


def test_creates_all_tables():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert "channels" in tables
        assert "tasks" in tables
        assert "signals" in tables
        assert "snapshots" in tables
        assert "outcomes" in tables
        assert "push_logs" in tables
        assert "screenshots" in tables


def test_channels_schema():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(channels)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()

        assert "id" in columns
        assert "type" in columns
        assert "name" in columns
        assert "config_json" in columns
        assert "enabled" in columns


def test_tasks_schema():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(tasks)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()

        assert "id" in columns
        assert "name" in columns
        assert "type" in columns
        assert "config_json" in columns
        assert "actions_json" in columns
        assert "channel_id" in columns
        assert "schedule" in columns
        assert "enabled" in columns


def test_signals_schema():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(signals)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()

        assert "id" in columns
        assert "task_id" in columns
        assert "symbol" in columns
        assert "exchange" in columns
        assert "indicator" in columns
        assert "timeframe" in columns
        assert "signal_type" in columns
        assert "triggered_at" in columns


def test_outcomes_schema():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(outcomes)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()

        assert "price_1h" in columns
        assert "price_4h" in columns
        assert "price_24h" in columns
        assert "change_1h" in columns
        assert "change_4h" in columns
        assert "change_24h" in columns


def test_init_db_is_idempotent():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        init_db(db_path)
        init_db(db_path)  # should not raise
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        # Exclude internal SQLite tables (e.g. sqlite_sequence created by AUTOINCREMENT)
        tables = {row[0] for row in cursor.fetchall() if not row[0].startswith("sqlite_")}
        conn.close()
        # 7 core tables (channels, tasks, signals, snapshots, outcomes, push_logs,
        # screenshots) + 2 trading tables (trading_credentials, trading_orders)
        # + 3 agent tables (outcome_checks, agent_runs, agent_decisions)
        # + 1 agent config table (agent_config)
        assert len(tables) == 13
