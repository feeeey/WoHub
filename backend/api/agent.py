import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Literal
from agent.config import load_config, save_config
from config import settings
from database import get_db

router = APIRouter(prefix="/agent")


class AgentConfigBody(BaseModel):
    provider: Literal["openai", "anthropic"]
    base_url: str = ""
    api_key: Optional[str] = None          # None = 不改
    model: str
    max_tokens: int = Field(4096, ge=256, le=64000)
    max_tool_calls: int = Field(15, ge=1, le=50)
    deep_dive_limit: int = Field(5, ge=0, le=20)
    cooldown_minutes: int = Field(240, ge=0)
    credential_id: Optional[int] = None
    push_verdict: bool = False
    enabled: bool = False


def _public(cfg) -> dict:
    d = cfg.__dict__.copy()
    d["has_api_key"] = bool(d.pop("api_key"))
    d["insecure_defaults"] = settings.insecure_defaults()   # 前端据此显示警告
    return d


@router.get("/config")
def get_config():
    return _public(load_config())


@router.put("/config")
def put_config(body: AgentConfigBody):
    save_config(body.model_dump())
    return _public(load_config())


@router.get("/runs")
def list_runs(limit: int = 50):
    db = get_db(settings.db_path)
    try:
        rows = db.execute(
            """SELECT r.id, r.task_id, r.decider, r.status, r.model, r.prompt_version,
                      r.input_tokens, r.output_tokens, r.error, r.created_at, r.finished_at,
                      t.name AS task_name,
                      (SELECT COUNT(*) FROM agent_decisions d WHERE d.run_id = r.id) AS decision_count
               FROM agent_runs r LEFT JOIN tasks t ON t.id = r.task_id
               WHERE r.decider = 'agent'
               ORDER BY r.id DESC LIMIT ?""", (limit,)).fetchall()
    finally:
        db.close()
    return [dict(r) for r in rows]


@router.get("/runs/{run_id}")
def run_detail(run_id: int):
    db = get_db(settings.db_path)
    try:
        run = db.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        if not run:
            raise HTTPException(404, "run not found")
        ds = db.execute(
            """SELECT d.*, o.change_1h, o.change_4h, o.change_24h
               FROM agent_decisions d LEFT JOIN outcomes o ON o.signal_id = d.signal_id
               WHERE d.run_id = ? ORDER BY d.id""", (run_id,)).fetchall()
    finally:
        db.close()
    out = dict(run)
    out["trace"] = json.loads(run["trace_json"]) if run["trace_json"] else None
    out["context"] = json.loads(run["context_json"]) if run["context_json"] else None
    out.pop("trace_json")
    out.pop("context_json")
    out["decisions"] = [dict(d) for d in ds]
    return out


@router.post("/runs/{run_id}/rerun")
def rerun(run_id: int):
    db = get_db(settings.db_path)
    try:
        row = db.execute("SELECT task_id, context_json FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
    finally:
        db.close()
    if not row:
        raise HTTPException(404, "run not found")
    from agent.queue import enqueue_run
    new_id = enqueue_run(row["task_id"], json.loads(row["context_json"] or "{}"))
    return {"id": new_id}


class RateBody(BaseModel):
    rating: Literal[-1, 0, 1]


@router.post("/decisions/{decision_id}/rate")
def rate_decision(decision_id: int, body: RateBody):
    db = get_db(settings.db_path)
    try:
        cur = db.execute("UPDATE agent_decisions SET human_rating = ? WHERE id = ?",
                         (body.rating, decision_id))
        db.commit()
    finally:
        db.close()
    if cur.rowcount == 0:
        raise HTTPException(404, "decision not found")
    return {"ok": True}
