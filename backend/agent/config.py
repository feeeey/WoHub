# backend/agent/config.py
"""Single-row agent config. API key is Fernet-encrypted with the same
SECRET_KEY-derived key as trading credentials — rotating SECRET_KEY
invalidates the stored key (documented operator behavior)."""
from dataclasses import dataclass
from database import get_db
from config import settings
from trading.credentials import encrypt_secret, decrypt_secret

FIELDS = ("provider", "base_url", "model", "vision_model", "max_tokens", "max_tool_calls",
          "deep_dive_limit", "cooldown_minutes", "credential_id", "push_verdict", "enabled")


@dataclass
class AgentConfig:
    provider: str
    base_url: str
    api_key: str | None
    model: str
    vision_model: str
    max_tokens: int
    max_tool_calls: int
    deep_dive_limit: int
    cooldown_minutes: int
    credential_id: int | None
    push_verdict: bool
    enabled: bool


def _ensure_row(db):
    db.execute("INSERT OR IGNORE INTO agent_config (id) VALUES (1)")


def load_config() -> AgentConfig:
    db = get_db(settings.db_path)
    try:
        _ensure_row(db)
        db.commit()
        row = dict(db.execute("SELECT * FROM agent_config WHERE id = 1").fetchone())
    finally:
        db.close()
    key = decrypt_secret(row["api_key_enc"]) if row["api_key_enc"] else None
    return AgentConfig(provider=row["provider"], base_url=row["base_url"], api_key=key,
                       model=row["model"], vision_model=row["vision_model"],
                       max_tokens=row["max_tokens"],
                       max_tool_calls=row["max_tool_calls"], deep_dive_limit=row["deep_dive_limit"],
                       cooldown_minutes=row["cooldown_minutes"], credential_id=row["credential_id"],
                       push_verdict=bool(row["push_verdict"]), enabled=bool(row["enabled"]))


def save_config(data: dict) -> None:
    """data: FIELDS 子集 + 可选 api_key（None/缺省 = 不改动已存密钥）。
    credential_id 允许显式置 None（清除）。"""
    db = get_db(settings.db_path)
    try:
        _ensure_row(db)
        sets, params = [], []
        for f in FIELDS:
            if f not in data:
                continue
            if data[f] is None and f != "credential_id":
                continue
            sets.append(f"{f} = ?")
            v = data[f]
            params.append(int(v) if isinstance(v, bool) else v)
        # 空串 = 显式清除已存密钥；None/缺省 = 不改动
        if "api_key" in data and data["api_key"] == "":
            sets.append("api_key_enc = ?")
            params.append(None)
        elif data.get("api_key"):
            sets.append("api_key_enc = ?")
            params.append(encrypt_secret(data["api_key"]))
        if sets:
            sets.append("updated_at = datetime('now')")
            db.execute(f"UPDATE agent_config SET {', '.join(sets)} WHERE id = 1", params)
        db.commit()
    finally:
        db.close()
