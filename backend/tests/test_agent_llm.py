import pytest
from agent.config import Channel


def _ch(**kw):
    base = dict(id=1, name="test", provider="openai",
                base_url="https://gw.example.com/v1", api_key="k")
    base.update(kw)
    return Channel(**base)


def test_openai_model_uses_base_url():
    from agent.llm import build_model
    m = build_model(_ch(), "gpt-5")
    assert m.model_name == "gpt-5"


def test_anthropic_model():
    from agent.llm import build_model
    m = build_model(_ch(provider="anthropic", base_url=""), "claude-sonnet-4-6")
    assert "claude" in m.model_name


def test_missing_key_raises():
    from agent.llm import build_model
    with pytest.raises(ValueError):
        build_model(_ch(api_key=None), "gpt-5")


def test_missing_channel_raises():
    from agent.llm import build_model
    with pytest.raises(ValueError):
        build_model(None, "gpt-5")


def test_empty_model_name_raises():
    from agent.llm import build_model
    with pytest.raises(ValueError):
        build_model(_ch(), "")
