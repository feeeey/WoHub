from database import get_db
from config import settings

DEFAULT_STRATEGY_NAME = "默认技术分析师"
DEFAULT_STRATEGY_PROMPT = """你是一个专业的加密货币技术分析师。你只基于价格走势、成交量、技术指标进行分析，不考虑任何消息面因素。

你的分析应该包括：
1. 当前信号的含义解读
2. 结合历史数据的可靠性评估
3. 关键支撑位和阻力位（如果截图可见）
4. 短期方向判断（看涨/看跌/中性）
5. 风险提示

保持简洁，每次分析控制在 200 字以内。使用中文回复。"""


def ensure_default_strategy():
    db = get_db(settings.db_path)
    row = db.execute("SELECT id FROM strategies WHERE is_default = 1").fetchone()
    if not row:
        db.execute(
            "INSERT INTO strategies (name, system_prompt, is_default) VALUES (?, ?, 1)",
            (DEFAULT_STRATEGY_NAME, DEFAULT_STRATEGY_PROMPT),
        )
        db.commit()
    db.close()


def get_default_strategy():
    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM strategies WHERE is_default = 1").fetchone()
    db.close()
    if row:
        return {"id": row["id"], "name": row["name"], "system_prompt": row["system_prompt"]}
    return {"id": 0, "name": DEFAULT_STRATEGY_NAME, "system_prompt": DEFAULT_STRATEGY_PROMPT}


def get_ai_config():
    db = get_db(settings.db_path)
    rows = db.execute("SELECT key, value FROM ai_config").fetchall()
    db.close()
    config = {r["key"]: r["value"] for r in rows}
    return {
        "api_key": config.get("api_key", ""),
        "base_url": config.get("base_url", "https://api.openai.com/v1"),
        "model": config.get("model", "gpt-4o"),
        "max_tokens": int(config.get("max_tokens", "1000")),
    }


def set_ai_config(data: dict):
    db = get_db(settings.db_path)
    for key, value in data.items():
        if key in ("api_key", "base_url", "model", "max_tokens"):
            db.execute(
                "INSERT INTO ai_config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = datetime('now')",
                (key, str(value), str(value)),
            )
    db.commit()
    db.close()
