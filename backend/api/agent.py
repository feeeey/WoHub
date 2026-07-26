import base64
import sqlite3
import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Literal
from agent.config import (load_config, save_config, Channel, list_channels,
                          get_channel, save_channel, channel_in_use, delete_channel)
from agent.llm import build_model
from config import settings

router = APIRouter(prefix="/agent")


class AgentConfigBody(BaseModel):
    channel_id: Optional[int] = None
    vision_channel_id: Optional[int] = None
    model: str
    vision_model: str = ""
    max_tokens: int = Field(4096, ge=256, le=64000)
    max_tool_calls: int = Field(15, ge=1, le=50)
    deep_dive_limit: int = Field(5, ge=0, le=20)
    credential_id: Optional[int] = None
    enabled: bool = False


def _public(cfg) -> dict:
    d = cfg.__dict__.copy()
    main = d.pop("main_channel")
    d.pop("vision_channel")
    d["has_api_key"] = bool(main and main.api_key)
    d["insecure_defaults"] = settings.insecure_defaults()   # 前端据此显示警告
    return d


@router.get("/config")
def get_config():
    return _public(load_config())


@router.put("/config")
def put_config(body: AgentConfigBody):
    save_config(body.model_dump(exclude_unset=True))
    return _public(load_config())


# ---- LLM 渠道 CRUD ----

class ChannelBody(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    provider: Literal["openai", "anthropic"] = "openai"
    base_url: str = ""
    api_key: Optional[str] = None      # None = 不改, "" = 清除


@router.get("/channels")
def get_channels():
    return {"channels": list_channels()}


@router.post("/channels")
def create_channel(body: ChannelBody):
    try:
        return {"id": save_channel(body.model_dump())}
    except sqlite3.IntegrityError:
        raise HTTPException(409, "渠道名已存在")


@router.put("/channels/{channel_id}")
def update_channel(channel_id: int, body: ChannelBody):
    if get_channel(channel_id) is None:
        raise HTTPException(404, "渠道不存在")
    try:
        save_channel({**body.model_dump(), "id": channel_id})
    except sqlite3.IntegrityError:
        raise HTTPException(409, "渠道名已存在")
    return {"id": channel_id}


@router.delete("/channels/{channel_id}")
def remove_channel(channel_id: int):
    if get_channel(channel_id) is None:
        raise HTTPException(404, "渠道不存在")
    if channel_in_use(channel_id):
        raise HTTPException(409, "渠道正被主模型或视觉槽位引用，请先切换槽位")
    delete_channel(channel_id)
    return {"ok": True}


# ---- 连通性探测与模型列表（Phase 6）----

# 1x1 红色 PNG，用于视觉模型图像能力探测
_PROBE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/"
    "q842iQAAAABJRU5ErkJggg==")


class ProbeBody(BaseModel):
    channel_id: Optional[int] = None
    provider: Optional[Literal["openai", "anthropic"]] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None


def _resolve_channel(body: ProbeBody) -> Channel:
    """已存渠道为底 + inline 覆盖（支持渠道未保存先测）。

    红线：**已存的 API Key 只能发往它自己配的 base_url**。否则
    `{"channel_id": 1, "base_url": "http://任意地址"}` 就能让服务端带着解密后的
    密钥去连任意 URL——既是凭据外泄，也是打进内网的 SSRF 跳板。想换地址测就必须
    连 Key 一起给（那是调用方自己的密钥，爱发哪发哪）。
    """
    base = None
    if body.channel_id:
        base = get_channel(body.channel_id)
        if base is None:
            raise HTTPException(404, "渠道不存在")

    inline_key = body.api_key is not None
    if (base and body.base_url is not None
            and body.base_url != base.base_url and not inline_key):
        raise HTTPException(
            400, "改用其他 base_url 测试时必须同时提供 API Key："
                 "已保存的密钥不会被发往它所属地址以外的任何 URL")

    return Channel(
        id=base.id if base else 0,
        name=base.name if base else "(未保存)",
        provider=body.provider or (base.provider if base else "openai"),
        base_url=(base.base_url if base else "") if body.base_url is None else body.base_url,
        api_key=(base.api_key if base else None) if not inline_key else body.api_key)


@router.post("/models")
def list_models(body: ProbeBody):
    ch = _resolve_channel(body)
    if not ch.api_key:
        raise HTTPException(400, "未配置 API Key")
    try:
        if ch.provider == "anthropic":
            r = requests.get("https://api.anthropic.com/v1/models",
                             headers={"x-api-key": ch.api_key,
                                      "anthropic-version": "2023-06-01"}, timeout=15)
        else:
            base = (ch.base_url or "https://api.openai.com/v1").rstrip("/")
            r = requests.get(f"{base}/models",
                             headers={"Authorization": f"Bearer {ch.api_key}"}, timeout=15)
        r.raise_for_status()
        ids = sorted(m["id"] for m in r.json().get("data", []) if m.get("id"))
        return {"models": ids}
    except requests.RequestException as e:
        raise HTTPException(502, f"模型列表获取失败: {e}")


def _probe_text(channel, model_name) -> dict:
    """最小文本调用验证渠道×模型可用。真网调用，仅由 /test 端点触发。"""
    try:
        from pydantic_ai import Agent
        agent = Agent(build_model(channel, model_name), output_type=str)
        # 思考型模型（如 deepseek-v4-pro）先消耗推理 token 再输出——
        # 预算必须容纳整段思考，太小会在产出任何文字前被截断
        agent.run_sync("回复一个字：好", model_settings={"max_tokens": 2048})
        return {"ok": True, "channel": channel.name}
    except Exception as e:
        return {"ok": False, "channel": channel.name, "error": str(e)[:300]}


def _probe_vision(channel, model_name) -> dict:
    try:
        from pydantic_ai import Agent, BinaryContent
        agent = Agent(build_model(channel, model_name), output_type=str)
        agent.run_sync(["图中是什么颜色？一词回答。",
                        BinaryContent(data=_PROBE_PNG, media_type="image/png")],
                       model_settings={"max_tokens": 2048})
        return {"ok": True, "channel": channel.name, "supports_image": True}
    except Exception as e:
        return {"ok": False, "channel": channel.name, "error": str(e)[:300]}


class TestBody(BaseModel):
    channel_id: Optional[int] = None
    model: Optional[str] = None
    vision_channel_id: Optional[int] = None
    vision_model: Optional[str] = None


@router.post("/test")
def test_llm(body: TestBody):
    cfg = load_config()
    main_ch = get_channel(body.channel_id) if body.channel_id else cfg.main_channel
    model = body.model or cfg.model
    if main_ch is None or not main_ch.api_key:
        raise HTTPException(400, "主渠道未配置或缺少 API Key")
    out = {"main": _probe_text(main_ch, model), "vision": None}
    vision_model = cfg.vision_model if body.vision_model is None else body.vision_model
    if vision_model:
        vch = (get_channel(body.vision_channel_id) if body.vision_channel_id
               else (cfg.vision_channel if cfg.vision_channel_id else main_ch))
        if vch is None or not vch.api_key:
            out["vision"] = {"ok": False, "channel": "-",
                             "error": "视觉渠道未配置或缺少 API Key"}
        else:
            out["vision"] = _probe_vision(vch, vision_model)
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


@router.post("/semantics/validate")
def validate_semantics():
    """用真实后验数据审计语义档案的方向声明（OutcomeValidator）。

    只有 SCREENER_BIAS 里映射了明确 long/short 的筛选器可验证；
    双向/中性的档案没有可检验的方向声明，如实返回 skipped。
    """
    from agent.chat.semantics import get_all
    from agent.decider import SCREENER_BIAS
    from agent.validator import OutcomeValidator

    rows = get_all()
    biased = [(r, SCREENER_BIAS[r["key"]]) for r in rows if r["key"] in SCREENER_BIAS]
    results = [{"key": r["key"], "label": r["label"], "verdict": "skipped",
                "detail": "无明确方向声明（双向/中性），不可检验"}
               for r in rows if r["key"] not in SCREENER_BIAS]

    if biased:
        # 全部方向声明装进一个 spec 一次验证：Bonferroni 按本次审计的
        # 检验总数校正——逐个单独验证会低估多重检验的假阳性率
        report = OutcomeValidator().validate({
            "name": "semantics-audit", "sample_window": "90d",
            "rules": [{"label": r["label"], "bias": b} for r, b in biased]})
        by_label = {m["label"]: m for m in report.metrics.get("rules", [])}
        for r, bias in biased:
            m = by_label.get(r["label"], {})
            results.append({"key": r["key"], "label": r["label"], "bias": bias,
                            "verdict": m.get("verdict", "not_validated"),
                            **{k: m[k] for k in ("n", "hit_rate", "p_value",
                               "alpha_adjusted", "fold_hit_rates", "detail")
                               if k in m}})

    key_order = {r["key"]: i for i, r in enumerate(rows)}
    results.sort(key=lambda x: key_order.get(x["key"], 99))
    return {"results": results,
            "note": "pass=方向声明与后验分布显著一致（≠按此交易可盈利）；"
                    "not_validated=样本不足，拒绝背书；已按检验数做 Bonferroni 校正"}


@router.get("/usage")
def get_usage(days: int = 30):
    """agent 用量观测：token 按日/按模型聚合 + 工具调用与失败率（来自 trace）。

    token 数是模型 API 返回的原始计数，一直在逐消息落库但从未聚合——
    这里只是把已有数据变可见，没有新的采集面。
    """
    import json as _json
    from database import get_db

    days = max(1, min(int(days), 365))
    db = get_db(settings.db_path)
    try:
        daily = [dict(r) for r in db.execute(
            """SELECT date(created_at) AS day,
                      COUNT(*) AS turns,
                      COALESCE(SUM(input_tokens), 0) AS input_tokens,
                      COALESCE(SUM(output_tokens), 0) AS output_tokens
               FROM chat_messages
               WHERE role = 'assistant' AND created_at >= datetime('now', ?)
               GROUP BY day ORDER BY day DESC""",
            (f"-{days} days",)).fetchall()]
        by_model = [dict(r) for r in db.execute(
            """SELECT COALESCE(model, 'unknown') AS model,
                      COUNT(*) AS turns,
                      COALESCE(SUM(input_tokens), 0) AS input_tokens,
                      COALESCE(SUM(output_tokens), 0) AS output_tokens
               FROM chat_messages
               WHERE role = 'assistant' AND created_at >= datetime('now', ?)
               GROUP BY model ORDER BY input_tokens DESC""",
            (f"-{days} days",)).fetchall()]
        traces = db.execute(
            """SELECT trace_json FROM chat_messages
               WHERE role = 'assistant' AND trace_json IS NOT NULL
                 AND created_at >= datetime('now', ?)""",
            (f"-{days} days",)).fetchall()
    finally:
        db.close()

    tools: dict[str, dict] = {}
    for r in traces:
        try:
            steps = _json.loads(r["trace_json"]).get("steps") or []
        except _json.JSONDecodeError:
            continue
        for s in steps:
            name = s.get("tool")
            if not name:
                continue
            t = tools.setdefault(name, {"calls": 0, "errors": 0})
            t["calls"] += 1
            if '"error"' in (s.get("result") or "")[:120]:
                t["errors"] += 1
    tool_stats = [{"tool": k, **v,
                   "error_rate": round(v["errors"] / v["calls"], 4) if v["calls"] else 0}
                  for k, v in sorted(tools.items(), key=lambda x: -x[1]["calls"])]

    return {"window_days": days, "daily": daily, "by_model": by_model,
            "tools": tool_stats,
            "totals": {"turns": sum(d["turns"] for d in daily),
                       "input_tokens": sum(d["input_tokens"] for d in daily),
                       "output_tokens": sum(d["output_tokens"] for d in daily)}}


# ---- 长期记忆管理（写入走 chat 工具，这里只有查看/删除）----

@router.get("/memories")
def get_memories():
    from agent.memory import list_memories, MAX_MEMORIES
    rows = list_memories()
    return {"memories": rows, "count": len(rows), "max": MAX_MEMORIES}


@router.delete("/memories/{memory_id}")
def delete_memory(memory_id: int):
    from agent.memory import forget_memory
    out = forget_memory(memory_id)
    if out.get("error"):
        raise HTTPException(404, out["error"])
    return out
