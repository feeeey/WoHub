import sqlite3
import os

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    actions_json TEXT NOT NULL DEFAULT '[]',
    channel_id INTEGER REFERENCES channels(id),
    schedule TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER REFERENCES tasks(id),
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    indicator TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    signal_type TEXT,
    triggered_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER REFERENCES signals(id),
    price REAL,
    volume_24h REAL,
    change_24h REAL,
    funding_rate REAL,
    captured_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER REFERENCES signals(id),
    price_1h REAL,
    price_4h REAL,
    price_24h REAL,
    change_1h REAL,
    change_4h REAL,
    change_24h REAL,
    tracked_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS push_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER REFERENCES tasks(id),
    channel_id INTEGER REFERENCES channels(id),
    content_text TEXT,
    image_paths TEXT,
    status TEXT NOT NULL DEFAULT 'success',
    error_message TEXT,
    pushed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS screenshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER REFERENCES signals(id),
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    file_path TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trading_credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    env TEXT NOT NULL CHECK (env IN ('testnet', 'mainnet')),
    api_key TEXT NOT NULL,
    api_secret_enc TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trading_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    credential_id INTEGER REFERENCES trading_credentials(id) ON DELETE SET NULL,
    env TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL,
    leverage INTEGER,
    margin_type TEXT,
    binance_order_id TEXT,
    status TEXT NOT NULL,
    response_json TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS outcome_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER REFERENCES signals(id),
    horizon TEXT NOT NULL CHECK (horizon IN ('1h', '4h', '24h')),
    due_at TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_outcome_checks_due ON outcome_checks(done, due_at);

CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER REFERENCES tasks(id),
    decider TEXT NOT NULL CHECK (decider IN ('agent', 'rule')),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'done', 'failed')),
    context_json TEXT,
    trace_json TEXT,
    model TEXT,
    prompt_version TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_status ON agent_runs(status);
CREATE INDEX IF NOT EXISTS idx_agent_runs_task ON agent_runs(task_id, created_at);

CREATE TABLE IF NOT EXISTS agent_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES agent_runs(id),
    signal_id INTEGER REFERENCES signals(id),
    signal_ids_json TEXT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('long', 'short', 'skip')),
    confidence REAL,
    reasons TEXT,
    factors_json TEXT,
    human_rating INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_agent_decisions_symbol ON agent_decisions(symbol, timeframe, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_decisions_run ON agent_decisions(run_id);

CREATE TABLE IF NOT EXISTS agent_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    provider TEXT NOT NULL DEFAULT 'openai' CHECK (provider IN ('openai', 'anthropic')),
    base_url TEXT NOT NULL DEFAULT '',
    api_key_enc TEXT,
    model TEXT NOT NULL DEFAULT '',
    max_tokens INTEGER NOT NULL DEFAULT 4096,
    max_tool_calls INTEGER NOT NULL DEFAULT 15,
    deep_dive_limit INTEGER NOT NULL DEFAULT 5,
    cooldown_minutes INTEGER NOT NULL DEFAULT 240,
    credential_id INTEGER,
    push_verdict INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_outcomes_signal ON outcomes(signal_id);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '新会话',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES chat_sessions(id),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL DEFAULT '',
    images_json TEXT,
    trace_json TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    model TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, id);

CREATE TABLE IF NOT EXISTS chat_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES chat_sessions(id),
    user_message_id INTEGER REFERENCES chat_messages(id),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_chat_turns_status ON chat_turns(status);
CREATE INDEX IF NOT EXISTS idx_chat_turns_session ON chat_turns(session_id, id);

CREATE TABLE IF NOT EXISTS chat_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id INTEGER NOT NULL REFERENCES chat_turns(id),
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chat_events_turn ON chat_events(turn_id, seq);

CREATE TABLE IF NOT EXISTS screener_semantics (
    key TEXT PRIMARY KEY,
    meaning TEXT NOT NULL DEFAULT '',
    bias TEXT NOT NULL DEFAULT '',
    usage TEXT NOT NULL DEFAULT '',
    caveats TEXT NOT NULL DEFAULT '',
    combos TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS llm_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL DEFAULT 'openai' CHECK (provider IN ('openai', 'anthropic')),
    base_url TEXT NOT NULL DEFAULT '',
    api_key_enc TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent column additions for tables that already exist in deployed DBs.
    (SCHEMA 是 append-only：改已存在的 CREATE TABLE 体是静默 no-op。)"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(agent_config)")}
    if "vision_model" not in cols:
        conn.execute("ALTER TABLE agent_config ADD COLUMN vision_model TEXT NOT NULL DEFAULT ''")
    if "channel_id" not in cols:
        conn.execute("ALTER TABLE agent_config ADD COLUMN channel_id INTEGER")
    if "vision_channel_id" not in cols:
        conn.execute("ALTER TABLE agent_config ADD COLUMN vision_channel_id INTEGER")
    # 单渠道旧配置 → 「默认渠道」。仅在 llm_channels 为空时执行（幂等）。
    if conn.execute("SELECT COUNT(*) FROM llm_channels").fetchone()[0] == 0:
        row = conn.execute(
            "SELECT provider, base_url, api_key_enc FROM agent_config WHERE id = 1").fetchone()
        if row and row[2]:
            cur = conn.execute(
                "INSERT INTO llm_channels (name, provider, base_url, api_key_enc) "
                "VALUES ('默认渠道', ?, ?, ?)", (row[0], row[1], row[2]))
            conn.execute("UPDATE agent_config SET channel_id = ? WHERE id = 1",
                         (cur.lastrowid,))


def init_db(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    conn.close()


def get_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
