"""Provider factory. 基于 pydantic-ai v1.x（版本漂移处理见实施计划 Task 7）。"""
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider


def build_model(cfg, model_name: str | None = None):
    """构建 LLM 模型实例（Anthropic/OpenAI）。model_name 覆盖 cfg.model
    （视觉模型槽位用）。"""
    if not cfg.api_key:
        raise ValueError("agent LLM api_key 未配置")
    name = model_name or cfg.model
    if cfg.provider == "anthropic":
        return AnthropicModel(name, provider=AnthropicProvider(api_key=cfg.api_key))
    kwargs = {"api_key": cfg.api_key}
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    return OpenAIChatModel(name, provider=OpenAIProvider(**kwargs))
