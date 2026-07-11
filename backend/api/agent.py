import base64
import dataclasses
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
    provider: Literal["openai", "anthropic"]
    base_url: str = ""
    api_key: Optional[str] = None          # None = 不改
    model: str
    vision_model: str = ""
    max_tokens: int = Field(4096, ge=256, le=64000)
    max_tool_calls: int = Field(15, ge=1, le=50)
    deep_dive_limit: int = Field(5, ge=0, le=20)
    credential_id: Optional[int] = None
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
        # 思考型模型（如 deepseek-v4-pro）先消耗推理 token 再输出——
        # 预算必须容纳整段思考，太小会在产出任何文字前被截断
        agent.run_sync("回复一个字：好", model_settings={"max_tokens": 2048})
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def _probe_vision(cfg) -> dict:
    try:
        from pydantic_ai import Agent, BinaryContent
        agent = Agent(build_model(cfg, model_name=cfg.vision_model), output_type=str)
        agent.run_sync(["图中是什么颜色？一词回答。",
                        BinaryContent(data=_PROBE_PNG, media_type="image/png")],
                       model_settings={"max_tokens": 2048})
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
