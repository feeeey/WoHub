import os
import sqlite3


def _conn():
    return sqlite3.connect(os.environ["DB_PATH"])


def _cols(table):
    c = _conn()
    try:
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    finally:
        c.close()


def test_llm_channels_table_and_agent_config_columns():
    assert {"id", "name", "provider", "base_url", "api_key_enc", "created_at"} <= _cols("llm_channels")
    assert {"channel_id", "vision_channel_id"} <= _cols("agent_config")


def test_migrate_backfills_default_channel_idempotently():
    from database import init_db
    db_path = os.environ["DB_PATH"]
    c = _conn()
    c.execute("INSERT OR IGNORE INTO agent_config (id) VALUES (1)")
    c.execute("UPDATE agent_config SET provider='openai', "
              "base_url='https://openrouter.ai/api/v1', api_key_enc='enc-blob' WHERE id=1")
    c.commit(); c.close()

    init_db(db_path)                                   # 重跑迁移 → 回填
    c = _conn()
    ch = c.execute("SELECT id, name, provider, base_url, api_key_enc FROM llm_channels").fetchall()
    assert len(ch) == 1
    assert ch[0][1] == "默认渠道" and ch[0][3] == "https://openrouter.ai/api/v1" and ch[0][4] == "enc-blob"
    row = c.execute("SELECT channel_id, vision_channel_id FROM agent_config WHERE id=1").fetchone()
    assert row[0] == ch[0][0] and row[1] is None
    row2 = c.execute("SELECT api_key_enc FROM agent_config WHERE id=1").fetchone()
    assert row2[0] is None                              # 休眠源列已清空,防止删渠道后复活
    c.close()

    init_db(db_path)                                   # 再跑一次 → 幂等,不重复建
    c = _conn()
    assert c.execute("SELECT COUNT(*) FROM llm_channels").fetchone()[0] == 1
    c.close()


def test_deleted_backfilled_channel_stays_deleted():
    from database import init_db
    db_path = os.environ["DB_PATH"]
    c = _conn()
    c.execute("INSERT OR IGNORE INTO agent_config (id) VALUES (1)")
    c.execute("UPDATE agent_config SET provider='openai', api_key_enc='enc-blob' WHERE id=1")
    c.commit(); c.close()
    init_db(db_path)                       # 回填
    c = _conn()
    c.execute("UPDATE agent_config SET channel_id = NULL WHERE id = 1")
    c.execute("DELETE FROM llm_channels")
    c.commit(); c.close()
    init_db(db_path)                       # 重启:不得复活
    c = _conn()
    assert c.execute("SELECT COUNT(*) FROM llm_channels").fetchone()[0] == 0
    c.close()


def test_no_key_no_backfill():
    from database import init_db
    init_db(os.environ["DB_PATH"])                     # 干净库(conftest 已建),无 key
    c = _conn()
    assert c.execute("SELECT COUNT(*) FROM llm_channels").fetchone()[0] == 0
    c.close()
