import pytest
from unittest.mock import patch, MagicMock
from ai.llm_client import LLMClient


def test_build_headers():
    client = LLMClient(api_key="sk-test", base_url="https://api.example.com/v1")
    headers = client._headers()
    assert headers["Authorization"] == "Bearer sk-test"
    assert "application/json" in headers["Content-Type"]


def test_build_request_body():
    client = LLMClient(api_key="sk-test", base_url="https://api.example.com/v1", model="gpt-4o")
    body = client._build_body(
        [{"role": "user", "content": "hello"}],
        stream=True,
    )
    assert body["model"] == "gpt-4o"
    assert body["stream"] is True
    assert body["messages"][0]["content"] == "hello"


def test_build_body_with_max_tokens():
    client = LLMClient(api_key="k", base_url="u", model="m", max_tokens=500)
    body = client._build_body([{"role": "user", "content": "hi"}], stream=False)
    assert body["max_tokens"] == 500


def test_from_config():
    with patch("ai.llm_client.get_ai_config", return_value={
        "api_key": "sk-abc", "base_url": "https://x.com/v1",
        "model": "gpt-4o-mini", "max_tokens": 800,
    }):
        client = LLMClient.from_config()
        assert client.api_key == "sk-abc"
        assert client.model == "gpt-4o-mini"
