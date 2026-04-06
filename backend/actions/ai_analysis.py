from ai.llm_client import LLMClient
from ai.context_builder import build_context
from ai.strategy import get_default_strategy, get_ai_config
from database import get_db
from config import settings


def run_ai_analysis(signal_id: int) -> str:
    conf = get_ai_config()
    if not conf["api_key"]:
        return ""

    strategy = get_default_strategy()
    context = build_context(signal_id)
    if not context:
        return ""

    messages = [{"role": "system", "content": strategy["system_prompt"]}] + context

    try:
        client = LLMClient.from_config()
        analysis = client.chat(messages)
    except Exception as e:
        print(f"[ai] LLM call failed: {e}")
        return f"AI 分析失败: {e}"

    sentiment = "neutral"
    lower = analysis.lower()
    if any(w in lower for w in ["看涨", "偏多", "bullish", "上涨"]):
        sentiment = "bullish"
    elif any(w in lower for w in ["看跌", "偏空", "bearish", "下跌"]):
        sentiment = "bearish"

    db = get_db(settings.db_path)
    db.execute(
        "INSERT INTO ai_analyses (signal_id, strategy_id, analysis_text, sentiment) VALUES (?, ?, ?, ?)",
        (signal_id, strategy["id"], analysis, sentiment),
    )
    db.commit()
    db.close()

    return analysis
