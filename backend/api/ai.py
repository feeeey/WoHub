import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from database import get_db
from config import settings
from ai.strategy import get_ai_config, set_ai_config, ensure_default_strategy, get_default_strategy

router = APIRouter(prefix="/ai")


class AIConfigUpdate(BaseModel):
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    max_tokens: Optional[int] = None


class StrategyCreate(BaseModel):
    name: str
    system_prompt: str


class StrategyUpdate(BaseModel):
    name: Optional[str] = None
    system_prompt: Optional[str] = None


# --- AI Config ---

@router.get("/config")
def ai_config():
    conf = get_ai_config()
    # Mask API key for display
    if conf["api_key"]:
        conf["api_key_display"] = conf["api_key"][:8] + "..." + conf["api_key"][-4:] if len(conf["api_key"]) > 16 else "****"
        conf["has_key"] = True
    else:
        conf["api_key_display"] = ""
        conf["has_key"] = False
    del conf["api_key"]
    return conf


@router.put("/config")
def update_ai_config(body: AIConfigUpdate):
    data = {k: v for k, v in body.dict().items() if v is not None}
    set_ai_config(data)
    return {"ok": True}


@router.post("/test")
def test_ai_connection():
    conf = get_ai_config()
    if not conf["api_key"]:
        return {"ok": False, "error": "API Key not configured"}
    try:
        import httpx
        resp = httpx.get(
            f"{conf['base_url']}/models",
            headers={"Authorization": f"Bearer {conf['api_key']}"},
            timeout=10,
        )
        if resp.status_code == 200:
            return {"ok": True, "models_count": len(resp.json().get("data", []))}
        return {"ok": False, "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# --- Strategies ---

@router.get("/strategies")
def list_strategies():
    ensure_default_strategy()
    db = get_db(settings.db_path)
    rows = db.execute("SELECT * FROM strategies ORDER BY is_default DESC, created_at DESC").fetchall()
    db.close()
    return [
        {"id": r["id"], "name": r["name"], "system_prompt": r["system_prompt"],
         "is_default": bool(r["is_default"]), "created_at": r["created_at"]}
        for r in rows
    ]


@router.post("/strategies")
def create_strategy(body: StrategyCreate):
    db = get_db(settings.db_path)
    cursor = db.execute(
        "INSERT INTO strategies (name, system_prompt) VALUES (?, ?)",
        (body.name, body.system_prompt),
    )
    db.commit()
    row = db.execute("SELECT * FROM strategies WHERE id = ?", (cursor.lastrowid,)).fetchone()
    db.close()
    return {"id": row["id"], "name": row["name"], "system_prompt": row["system_prompt"],
            "is_default": bool(row["is_default"]), "created_at": row["created_at"]}


@router.put("/strategies/{strategy_id}")
def update_strategy(strategy_id: int, body: StrategyUpdate):
    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "Strategy not found")
    updates, params = [], []
    if body.name is not None:
        updates.append("name = ?"); params.append(body.name)
    if body.system_prompt is not None:
        updates.append("system_prompt = ?"); params.append(body.system_prompt)
    if updates:
        updates.append("updated_at = datetime('now')")
        params.append(strategy_id)
        db.execute(f"UPDATE strategies SET {', '.join(updates)} WHERE id = ?", params)
        db.commit()
    row = db.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
    db.close()
    return {"id": row["id"], "name": row["name"], "system_prompt": row["system_prompt"],
            "is_default": bool(row["is_default"]), "created_at": row["created_at"]}


@router.delete("/strategies/{strategy_id}")
def delete_strategy(strategy_id: int):
    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "Strategy not found")
    if row["is_default"]:
        db.close()
        raise HTTPException(400, "Cannot delete default strategy")
    db.execute("DELETE FROM strategies WHERE id = ?", (strategy_id,))
    db.commit()
    db.close()
    return {"ok": True}


@router.post("/strategies/{strategy_id}/default")
def set_default_strategy(strategy_id: int):
    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "Strategy not found")
    db.execute("UPDATE strategies SET is_default = 0")
    db.execute("UPDATE strategies SET is_default = 1 WHERE id = ?", (strategy_id,))
    db.commit()
    row = db.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
    db.close()
    return {"id": row["id"], "name": row["name"], "system_prompt": row["system_prompt"],
            "is_default": bool(row["is_default"]), "created_at": row["created_at"]}


# --- Signals for AI analysis ---

@router.get("/signals")
def list_signals():
    db = get_db(settings.db_path)
    rows = db.execute("""
        SELECT s.*,
               snap.price, snap.volume_24h, snap.change_24h, snap.funding_rate,
               a.id as analysis_id, a.analysis_text, a.sentiment
        FROM signals s
        LEFT JOIN snapshots snap ON snap.signal_id = s.id
        LEFT JOIN ai_analyses a ON a.signal_id = s.id
        ORDER BY s.triggered_at DESC
        LIMIT 50
    """).fetchall()
    db.close()
    return [
        {
            "id": r["id"], "task_id": r["task_id"], "symbol": r["symbol"],
            "exchange": r["exchange"], "indicator": r["indicator"],
            "timeframe": r["timeframe"], "signal_type": r["signal_type"],
            "triggered_at": r["triggered_at"],
            "price": r["price"], "volume_24h": r["volume_24h"],
            "change_24h": r["change_24h"], "funding_rate": r["funding_rate"],
            "has_analysis": r["analysis_id"] is not None,
            "analysis_text": r["analysis_text"],
            "sentiment": r["sentiment"],
        }
        for r in rows
    ]


@router.get("/signals/{signal_id}")
def get_signal_detail(signal_id: int):
    db = get_db(settings.db_path)
    sig = db.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    if not sig:
        db.close()
        raise HTTPException(404, "Signal not found")

    snap = db.execute("SELECT * FROM snapshots WHERE signal_id = ?", (signal_id,)).fetchone()
    outcome = db.execute("SELECT * FROM outcomes WHERE signal_id = ?", (signal_id,)).fetchone()
    analysis = db.execute("SELECT * FROM ai_analyses WHERE signal_id = ? ORDER BY created_at DESC LIMIT 1", (signal_id,)).fetchone()

    # History: same symbol + indicator, last 10
    history = db.execute("""
        SELECT s.id, s.triggered_at, s.timeframe, o.change_1h, o.change_4h, o.change_24h
        FROM signals s
        LEFT JOIN outcomes o ON o.signal_id = s.id
        WHERE s.symbol = ? AND s.indicator = ? AND s.id != ?
        ORDER BY s.triggered_at DESC LIMIT 10
    """, (sig["symbol"], sig["indicator"], signal_id)).fetchall()

    db.close()

    return {
        "signal": dict(sig),
        "snapshot": dict(snap) if snap else None,
        "outcome": dict(outcome) if outcome else None,
        "analysis": {"text": analysis["analysis_text"], "sentiment": analysis["sentiment"],
                      "created_at": analysis["created_at"]} if analysis else None,
        "history": [dict(h) for h in history],
    }


@router.post("/analyze/{signal_id}")
def analyze_signal(signal_id: int):
    """Stream AI analysis for a signal via SSE."""
    from ai.llm_client import LLMClient
    from ai.context_builder import build_context

    conf = get_ai_config()
    if not conf["api_key"]:
        raise HTTPException(400, "API Key not configured")

    strategy = get_default_strategy()
    context = build_context(signal_id)
    if not context:
        raise HTTPException(404, "Signal not found or no context available")

    messages = [{"role": "system", "content": strategy["system_prompt"]}] + context

    def generate():
        full_text = []
        try:
            client = LLMClient.from_config()
            for chunk in client.stream_chat(messages):
                full_text.append(chunk)
                yield f"data: {json.dumps({'text': chunk})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

        # Save completed analysis
        analysis = "".join(full_text)
        if analysis:
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

        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
