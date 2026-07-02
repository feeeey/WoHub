"""Provider factory. 基于 pydantic-ai 1.107.0（已安装并验证以下 import 全部可用）。"""
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider


def build_model(cfg):
    if not cfg.api_key:
        raise ValueError("agent LLM api_key 未配置")
    if cfg.provider == "anthropic":
        return AnthropicModel(cfg.model, provider=AnthropicProvider(api_key=cfg.api_key))
    kwargs = {"api_key": cfg.api_key}
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    return OpenAIChatModel(cfg.model, provider=OpenAIProvider(**kwargs))
