import httpx
from pathlib import Path

TIMEOUT = 30
BASE = "https://discord.com/api/v10"


class DiscordChannel:
    def __init__(self, bot_token: str, channel_id: str):
        self.bot_token = bot_token
        self.channel_id = channel_id
        self._headers = {"Authorization": f"Bot {bot_token}"}

    def _check_response(self, resp, method):
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("message", resp.text)
            except Exception:
                detail = resp.text
            raise RuntimeError(f"Discord {method} failed ({resp.status_code}): {detail}")
        return resp.json()

    def send_text(self, text: str, channel_id: str = None) -> str:
        cid = channel_id or self.channel_id
        resp = httpx.post(
            f"{BASE}/channels/{cid}/messages",
            headers=self._headers,
            json={"content": text},
            timeout=TIMEOUT,
        )
        data = self._check_response(resp, "send_text")
        return data["id"]

    def send_photo(self, photo_path: str, caption: str = "", channel_id: str = None) -> str:
        cid = channel_id or self.channel_id
        with open(photo_path, "rb") as f:
            payload = {"content": caption} if caption else {}
            resp = httpx.post(
                f"{BASE}/channels/{cid}/messages",
                headers=self._headers,
                data=payload,
                files={"files[0]": ("chart.png", f, "image/png")},
                timeout=60,
            )
        data = self._check_response(resp, "send_photo")
        return data["id"]

    def test_connection(self) -> dict:
        try:
            # Verify bot token
            resp = httpx.get(
                f"{BASE}/users/@me",
                headers=self._headers,
                timeout=10,
            )
            if resp.status_code != 200:
                return {"ok": False, "error": f"Bot token invalid ({resp.status_code})"}
            bot = resp.json()
            bot_name = bot.get("username", "")

            # Verify channel access
            resp2 = httpx.get(
                f"{BASE}/channels/{self.channel_id}",
                headers=self._headers,
                timeout=10,
            )
            if resp2.status_code != 200:
                return {"ok": False, "error": f"无法访问频道 {self.channel_id}"}
            ch = resp2.json()
            channel_name = ch.get("name", "")

            return {"ok": True, "bot_name": bot_name, "channel_name": channel_name}
        except Exception as e:
            return {"ok": False, "error": str(e)}
