"""Provider factory. 基于 pydantic-ai v1.x（版本漂移处理见实施计划 Task 7）。"""
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider


def build_model(channel, model_name: str):
    """从「渠道 + 模型名」构建 LLM 实例。channel: agent.config.Channel。"""
    if channel is None or not channel.api_key:
        raise ValueError("LLM 渠道未配置或缺少 API Key")
    if not model_name:
        raise ValueError("模型名为空")
    if channel.provider == "anthropic":
        return AnthropicModel(model_name,
                              provider=AnthropicProvider(api_key=channel.api_key))
    kwargs = {"api_key": channel.api_key}
    if channel.base_url:
        kwargs["base_url"] = channel.base_url
    return OpenAIChatModel(model_name, provider=OpenAIProvider(**kwargs))
