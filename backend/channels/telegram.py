import httpx
from pathlib import Path

TIMEOUT = 30


class TelegramChannel:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._base = f"https://api.telegram.org/bot{bot_token}"

    def _resolve_chat(self, chat_id=None):
        return chat_id or self.chat_id

    def _check_response(self, resp, method):
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data.get('description', data)}")
        return data

    def send_text(self, text: str, chat_id: str = None, parse_mode: str = "HTML") -> int:
        resp = httpx.post(
            f"{self._base}/sendMessage",
            json={
                "chat_id": self._resolve_chat(chat_id),
                "text": text,
                "parse_mode": parse_mode,
            },
            timeout=TIMEOUT,
        )
        data = self._check_response(resp, "sendMessage")
        return data["result"]["message_id"]

    def edit_text(self, message_id: int, text: str, chat_id: str = None) -> None:
        resp = httpx.post(
            f"{self._base}/editMessageText",
            json={
                "chat_id": self._resolve_chat(chat_id),
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=TIMEOUT,
        )
        self._check_response(resp, "editMessageText")

    def send_photo(self, photo_path: str, caption: str = "", chat_id: str = None) -> int:
        with open(photo_path, "rb") as f:
            resp = httpx.post(
                f"{self._base}/sendPhoto",
                data={
                    "chat_id": self._resolve_chat(chat_id),
                    "caption": caption,
                    "parse_mode": "HTML",
                },
                files={"photo": ("chart.png", f, "image/png")},
                timeout=60,
            )
        data = self._check_response(resp, "sendPhoto")
        return data["result"]["message_id"]

    def edit_photo(self, message_id: int, photo_path: str, caption: str = "", chat_id: str = None) -> None:
        import json
        media = json.dumps({
            "type": "photo",
            "media": "attach://photo",
            "caption": caption,
            "parse_mode": "HTML",
        })
        with open(photo_path, "rb") as f:
            resp = httpx.post(
                f"{self._base}/editMessageMedia",
                data={
                    "chat_id": self._resolve_chat(chat_id),
                    "message_id": message_id,
                    "media": media,
                },
                files={"photo": ("chart.png", f, "image/png")},
                timeout=60,
            )
        self._check_response(resp, "editMessageMedia")

    def delete_message(self, message_id: int, chat_id: str = None) -> None:
        resp = httpx.post(
            f"{self._base}/deleteMessage",
            json={
                "chat_id": self._resolve_chat(chat_id),
                "message_id": message_id,
            },
            timeout=TIMEOUT,
        )
        self._check_response(resp, "deleteMessage")

    def test_connection(self) -> dict:
        try:
            resp = httpx.get(f"{self._base}/getMe", timeout=10)
            data = resp.json()
            if data.get("ok"):
                bot = data["result"]
                return {"ok": True, "bot_name": bot.get("first_name", ""), "username": bot.get("username", "")}
            return {"ok": False, "error": data.get("description", "Unknown error")}
        except Exception as e:
            return {"ok": False, "error": str(e)}
