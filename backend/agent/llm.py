"""Provider factory. 基于 pydantic-ai v1.x（版本漂移处理见实施计划 Task 7）。"""
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider


def build_model(cfg):
    """构建 LLM 模型实例（Anthropic/OpenAI）。

    注意：cfg.max_tokens/max_tool_calls 不在此应用——由 Task 12 的 Agent 构造
    （UsageLimits/model settings）负责。
    """
    if not cfg.api_key:
        raise ValueError("agent LLM api_key 未配置")
    if cfg.provider == "anthropic":
        return AnthropicModel(cfg.model, provider=AnthropicProvider(api_key=cfg.api_key))
    kwargs = {"api_key": cfg.api_key}
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    return OpenAIChatModel(cfg.model, provider=OpenAIProvider(**kwargs))
