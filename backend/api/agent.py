from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Optional, Literal
from agent.config import load_config, save_config
from config import settings

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
