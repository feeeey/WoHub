import os
import sqlite3


def _cols(db_path, table):
    conn = sqlite3.connect(db_path)
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_chat_tables_exist():
    db_path = os.environ["DB_PATH"]
    conn = sqlite3.connect(db_path)
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert {"chat_sessions", "chat_messages", "chat_turns",
            "chat_events", "screener_semantics"} <= names


def test_chat_events_columns():
    assert {"id", "turn_id", "seq", "type", "payload_json",
            "created_at"} <= _cols(os.environ["DB_PATH"], "chat_events")


def test_agent_config_vision_model_migrated():
    """init_db 对已存在的 agent_config 表也要补上 vision_model 列（幂等 ALTER）。"""
    assert "vision_model" in _cols(os.environ["DB_PATH"], "agent_config")


def test_migrate_idempotent():
    """重复 init_db 不报错（ALTER 已存在列时跳过）。"""
    from database import init_db
    init_db(os.environ["DB_PATH"])
    init_db(os.environ["DB_PATH"])
