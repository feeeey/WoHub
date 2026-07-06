import json
import base64
import dataclasses
import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Literal
from agent.config import load_config, save_config
from agent.llm import build_model
from config import settings
from database import get_db

router = APIRouter(prefix="/agent")

MIN_RELIABLE_N = 20   # 沿用量化层 P1 纪律：样本不足的格子显式标注


def _safe_json(s):
    try:
        return json.loads(s) if s else None
    except json.JSONDecodeError:
        return {"_parse_error": True}


class AgentConfigBody(BaseModel):
    provider: Literal["openai", "anthropic"]
    base_url: str = ""
    api_key: Optional[str] = None          # None = 不改
    model: str
    vision_model: str = ""
    max_tokens: int = Field(4096, ge=256, le=64000)
    max_tool_calls: int = Field(15, ge=1, le=50)
    deep_dive_limit: int = Field(5, ge=0, le=20)
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
    out["trace"] = _safe_json(run["trace_json"])
    out["context"] = _safe_json(run["context_json"])
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
        if cur.rowcount == 0:
            raise HTTPException(404, "decision not found")
    finally:
        db.close()
    return {"ok": True}


@router.get("/stats")
def stats():
    """方向感知胜率：long 赢=涨，short 赢=跌；skip 不计。
    按 decider × confidence 桶 × horizon 聚合；rule 基线 confidence 为 NULL，单独成桶。
    avg_signed_change: mean(sign × raw_change)，正值 = 平均而言方向正确。
    win 判定严格：change == 0 对多空双方都计为 loss。"""
    db = get_db(settings.db_path)
    try:
        rows = db.execute(
            """SELECT r.decider, d.direction, d.confidence,
                      o.change_1h, o.change_4h, o.change_24h
               FROM agent_decisions d
               JOIN agent_runs r ON r.id = d.run_id
               LEFT JOIN outcomes o ON o.signal_id = d.signal_id
               -- signal_id IS NULL 的裁决（信号落库失败的降级路径）无 outcome 可关联，不计入统计
               WHERE d.direction != 'skip' AND d.signal_id IS NOT NULL""").fetchall()
    finally:
        db.close()

    def bucket(conf):
        if conf is None:
            return "rule"
        if conf >= 0.7:
            return ">=0.7"
        if conf >= 0.5:
            return "0.5-0.7"
        return "<0.5"

    acc = {}   # (decider, bucket, horizon) -> [n, wins, sum_signed]
    for r in rows:
        b = bucket(r["confidence"])
        sign = 1 if r["direction"] == "long" else -1
        for h in ("1h", "4h", "24h"):
            chg = r[f"change_{h}"]
            if chg is None:
                continue
            key = (r["decider"], b, h)
            n, w, s = acc.get(key, (0, 0, 0.0))
            acc[key] = (n + 1, w + (1 if sign * chg > 0 else 0), s + sign * chg)  # strict: flat(0) = loss

    groups = [{"decider": k[0], "bucket": k[1], "horizon": k[2],
               "n": v[0], "wins": v[1],
               "win_rate": round(v[1] / v[0], 4) if v[0] else None,
               "avg_signed_change": round(v[2] / v[0], 4) if v[0] else None,
               "reliable": v[0] >= MIN_RELIABLE_N}
              for k, v in sorted(acc.items())]
    return {"groups": groups, "min_reliable_n": MIN_RELIABLE_N}


# ---- 连通性探测与模型列表（Phase 6）----

# 1x1 红色 PNG，用于视觉模型图像能力探测
_PROBE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/"
    "q842iQAAAABJRU5ErkJggg==")


class ProbeBody(BaseModel):
    provider: Optional[Literal["openai", "anthropic"]] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None       # None = 用已存密钥
    model: Optional[str] = None
    vision_model: Optional[str] = None


def _merged_cfg(body: ProbeBody):
    cfg = load_config()
    return dataclasses.replace(
        cfg,
        provider=body.provider or cfg.provider,
        base_url=cfg.base_url if body.base_url is None else body.base_url,
        api_key=body.api_key or cfg.api_key,
        model=body.model or cfg.model,
        vision_model=cfg.vision_model if body.vision_model is None else body.vision_model)


@router.post("/models")
def list_models(body: ProbeBody):
    cfg = _merged_cfg(body)
    if not cfg.api_key:
        raise HTTPException(400, "未配置 API Key")
    try:
        if cfg.provider == "anthropic":
            r = requests.get("https://api.anthropic.com/v1/models",
                             headers={"x-api-key": cfg.api_key,
                                      "anthropic-version": "2023-06-01"}, timeout=15)
        else:
            base = (cfg.base_url or "https://api.openai.com/v1").rstrip("/")
            r = requests.get(f"{base}/models",
                             headers={"Authorization": f"Bearer {cfg.api_key}"}, timeout=15)
        r.raise_for_status()
        ids = sorted(m["id"] for m in r.json().get("data", []) if m.get("id"))
        return {"models": ids}
    except requests.RequestException as e:
        raise HTTPException(502, f"模型列表获取失败: {e}")


def _probe_text(cfg) -> dict:
    """最小文本调用验证 key/model 可用。真网调用，仅由 /test 端点触发。"""
    try:
        from pydantic_ai import Agent
        agent = Agent(build_model(cfg), output_type=str)
        agent.run_sync("回复一个字：好", model_settings={"max_tokens": 16})
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def _probe_vision(cfg) -> dict:
    try:
        from pydantic_ai import Agent, BinaryContent
        agent = Agent(build_model(cfg, model_name=cfg.vision_model), output_type=str)
        agent.run_sync(["图中是什么颜色？一词回答。",
                        BinaryContent(data=_PROBE_PNG, media_type="image/png")],
                       model_settings={"max_tokens": 16})
        return {"ok": True, "supports_image": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


@router.post("/test")
def test_llm(body: ProbeBody):
    cfg = _merged_cfg(body)
    if not cfg.api_key:
        raise HTTPException(400, "未配置 API Key")
    out = {"main": _probe_text(cfg), "vision": None}
    if cfg.vision_model:
        out["vision"] = _probe_vision(cfg)
    return out


# ---- 筛选器语义档案 ----

class SemanticsBody(BaseModel):
    meaning: Optional[str] = None
    bias: Optional[str] = None
    usage: Optional[str] = None
    caveats: Optional[str] = None
    combos: Optional[str] = None


@router.get("/semantics")
def get_semantics():
    from agent.chat.semantics import seed_defaults, get_all
    seed_defaults()
    return get_all()


@router.put("/semantics/{folder}/{name}")
def put_semantics(folder: str, name: str, body: SemanticsBody):
    from agent.chat.semantics import upsert
    if not upsert(f"{folder}/{name}", body.model_dump()):
        raise HTTPException(404, "未知筛选器 key")
    return {"ok": True}
