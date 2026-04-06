import json
import httpx
from ai.strategy import get_ai_config
from config import settings


class LLMClient:
    def __init__(self, api_key: str, base_url: str, model: str = "gpt-4o", max_tokens: int = 1000):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_tokens = max_tokens

    @classmethod
    def from_config(cls):
        conf = get_ai_config()
        return cls(
            api_key=conf["api_key"],
            base_url=conf["base_url"],
            model=conf["model"],
            max_tokens=conf["max_tokens"],
        )

    def _proxy(self):
        if settings.proxy_enabled:
            url = f"http://{settings.proxy_host}:{settings.proxy_port}"
            return httpx.Proxy(url)
        return None

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _build_body(self, messages, stream=False):
        return {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "stream": stream,
        }

    def chat(self, messages):
        body = self._build_body(messages, stream=False)
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=body,
            timeout=60,
            proxy=self._proxy(),
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def stream_chat(self, messages):
        body = self._build_body(messages, stream=True)
        with httpx.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=body,
            timeout=120,
            proxy=self._proxy(),
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
