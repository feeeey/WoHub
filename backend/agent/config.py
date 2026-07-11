"""Single-row agent config + llm_channels CRUD. API keys are Fernet-encrypted
with the same SECRET_KEY-derived key as trading credentials — rotating
SECRET_KEY invalidates stored keys (documented operator behavior).
旧列 agent_config.provider/base_url/api_key_enc 已休眠：代码不再读写。"""
from dataclasses import dataclass
from database import get_db
from config import settings
from trading.credentials import encrypt_secret, decrypt_secret

FIELDS = ("channel_id", "vision_channel_id", "model", "vision_model", "max_tokens",
          "max_tool_calls", "deep_dive_limit", "cooldown_minutes", "credential_id",
          "push_verdict", "enabled")
_NULLABLE = {"credential_id", "channel_id", "vision_channel_id"}   # 允许显式置 NULL


@dataclass
class Channel:
    id: int
    name: str
    provider: str
    base_url: str
    api_key: str | None          # 解密后明文；None = 未配置


@dataclass
class AgentConfig:
    channel_id: int | None
    vision_channel_id: int | None
    model: str
    vision_model: str
    max_tokens: int
    max_tool_calls: int
    deep_dive_limit: int
    cooldown_minutes: int
    credential_id: int | None
    push_verdict: bool
    enabled: bool
    main_channel: Channel | None = None
    vision_channel: Channel | None = None    # vision_channel_id NULL 时 = main_channel


def _ensure_row(db):
    db.execute("INSERT OR IGNORE INTO agent_config (id) VALUES (1)")


def _row_to_channel(row) -> Channel:
    return Channel(id=row["id"], name=row["name"], provider=row["provider"],
                   base_url=row["base_url"],
                   api_key=decrypt_secret(row["api_key_enc"]) if row["api_key_enc"] else None)


def _fetch_channel(db, channel_id) -> Channel | None:
    row = db.execute("SELECT * FROM llm_channels WHERE id = ?", (channel_id,)).fetchone()
    return _row_to_channel(row) if row else None


def load_config() -> AgentConfig:
    db = get_db(settings.db_path)
    try:
        _ensure_row(db)
        db.commit()
        row = dict(db.execute("SELECT * FROM agent_config WHERE id = 1").fetchone())
        main = _fetch_channel(db, row["channel_id"]) if row["channel_id"] else None
        # 引用失效 → None；未设置 → 跟随主渠道
        vision = (_fetch_channel(db, row["vision_channel_id"])
                  if row["vision_channel_id"] else main)
    finally:
        db.close()
    return AgentConfig(
        channel_id=row["channel_id"], vision_channel_id=row["vision_channel_id"],
        model=row["model"], vision_model=row["vision_model"],
        max_tokens=row["max_tokens"], max_tool_calls=row["max_tool_calls"],
        deep_dive_limit=row["deep_dive_limit"], cooldown_minutes=row["cooldown_minutes"],
        credential_id=row["credential_id"], push_verdict=bool(row["push_verdict"]),
        enabled=bool(row["enabled"]), main_channel=main, vision_channel=vision)


def save_config(data: dict) -> None:
    """data: FIELDS 子集。_NULLABLE 中的字段允许显式置 None（清除）。"""
    db = get_db(settings.db_path)
    try:
        _ensure_row(db)
        sets, params = [], []
        for f in FIELDS:
            if f not in data:
                continue
            if data[f] is None and f not in _NULLABLE:
                continue
            sets.append(f"{f} = ?")
            v = data[f]
            params.append(int(v) if isinstance(v, bool) else v)
        if sets:
            sets.append("updated_at = datetime('now')")
            db.execute(f"UPDATE agent_config SET {', '.join(sets)} WHERE id = 1", params)
        db.commit()
    finally:
        db.close()


# ---- llm_channels CRUD ----

def list_channels() -> list[dict]:
    """公开形态：含 has_api_key，绝不含明文/密文。"""
    db = get_db(settings.db_path)
    try:
        rows = db.execute("SELECT id, name, provider, base_url, api_key_enc, created_at "
                          "FROM llm_channels ORDER BY id").fetchall()
    finally:
        db.close()
    return [{"id": r["id"], "name": r["name"], "provider": r["provider"],
             "base_url": r["base_url"], "has_api_key": bool(r["api_key_enc"]),
             "created_at": r["created_at"]} for r in rows]


def get_channel(channel_id: int) -> Channel | None:
    db = get_db(settings.db_path)
    try:
        return _fetch_channel(db, channel_id)
    finally:
        db.close()


def save_channel(data: dict) -> int:
    """data: name/provider/base_url + api_key（None=不改, ""=清除, 值=更新）。
    含 id = 更新，缺 id = 新建。返回渠道 id。name 冲突时抛 sqlite3.IntegrityError。"""
    key = data.get("api_key")
    db = get_db(settings.db_path)
    try:
        if data.get("id"):
            sets = ["name = ?", "provider = ?", "base_url = ?"]
            params = [data["name"], data["provider"], data.get("base_url", "")]
            if key == "":
                sets.append("api_key_enc = ?"); params.append(None)
            elif key:
                sets.append("api_key_enc = ?"); params.append(encrypt_secret(key))
            params.append(data["id"])
            db.execute(f"UPDATE llm_channels SET {', '.join(sets)} WHERE id = ?", params)
            cid = data["id"]
        else:
            cur = db.execute(
                "INSERT INTO llm_channels (name, provider, base_url, api_key_enc) "
                "VALUES (?, ?, ?, ?)",
                (data["name"], data["provider"], data.get("base_url", ""),
                 encrypt_secret(key) if key else None))
            cid = cur.lastrowid
        db.commit()
        return cid
    finally:
        db.close()


def channel_in_use(channel_id: int) -> bool:
    db = get_db(settings.db_path)
    try:
        row = db.execute("SELECT 1 FROM agent_config WHERE id = 1 AND "
                         "(channel_id = ? OR vision_channel_id = ?)",
                         (channel_id, channel_id)).fetchone()
        return row is not None
    finally:
        db.close()


def delete_channel(channel_id: int) -> None:
    db = get_db(settings.db_path)
    try:
        db.execute("DELETE FROM llm_channels WHERE id = ?", (channel_id,))
        db.commit()
    finally:
        db.close()
