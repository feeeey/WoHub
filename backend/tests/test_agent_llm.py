import pytest
from agent.config import AgentConfig


def _cfg(**kw):
    base = dict(provider="openai", base_url="https://gw.example.com/v1", api_key="k",
                model="gpt-5", vision_model="", max_tokens=4096, max_tool_calls=15,
                deep_dive_limit=5, cooldown_minutes=240, credential_id=None,
                push_verdict=False, enabled=True)
    base.update(kw)
    return AgentConfig(**base)


def test_openai_model_uses_base_url():
    from agent.llm import build_model
    m = build_model(_cfg())
    assert m.model_name == "gpt-5"


def test_anthropic_model():
    from agent.llm import build_model
    m = build_model(_cfg(provider="anthropic", base_url="", model="claude-sonnet-4-6"))
    assert "claude" in m.model_name


def test_missing_key_raises():
    from agent.llm import build_model
    with pytest.raises(ValueError):
        build_model(_cfg(api_key=None))


def test_prompt_version_exists():
    from agent.prompts import PROMPT_VERSION, SYSTEM_PROMPT
    assert PROMPT_VERSION and "纯技术分析" in SYSTEM_PROMPT


def test_render_batch_happy_path():
    from agent.prompts import render_batch
    out = render_batch({
        "task_id": 1, "task_type": "watchlist_signal",
        "results": [{"label": "底背离", "resolution": "1h", "symbols": []}],
        "candidates": [{"symbol": "BTCUSDT", "timeframe": "1h", "labels": ["底背离"],
                        "snapshot": {"priceChangePercent": 2.5, "fundingRate": 0.0001}}],
        "cross": {}, "bias_map": {"底背离": "long"},
    })
    assert "BTCUSDT" in out and "空集" in out and "long" in out


def test_render_batch_tolerates_missing_keys():
    from agent.prompts import render_batch
    out = render_batch({})
    assert "任务" in out   # 不抛 KeyError
