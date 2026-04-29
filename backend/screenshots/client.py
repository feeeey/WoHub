import requests
from config import settings


class ChartShotClient:
    def __init__(self, base_url=None):
        self.base_url = (base_url or settings.chartshot_url).rstrip("/")

    def health(self):
        resp = requests.get(f"{self.base_url}/health", timeout=5)
        resp.raise_for_status()
        return resp.json()

    def screenshot(self, symbol, timeframes, timeout=120):
        try:
            resp = requests.post(
                f"{self.base_url}/api/screenshot",
                json={"symbol": symbol, "timeframes": timeframes},
                timeout=timeout,
            )
            return resp.json()
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def screenshot_url(self, filename):
        return f"{self.base_url}/api/screenshot/file/{filename}"

    def get_cookies(self):
        resp = requests.get(f"{self.base_url}/api/cookies", timeout=5)
        return resp.json()

    def update_cookies(self, raw_cookie_string):
        resp = requests.put(
            f"{self.base_url}/api/cookies",
            json={"cookies": raw_cookie_string},
            timeout=5,
        )
        return resp.json()

    def test_cookies(self):
        resp = requests.post(f"{self.base_url}/api/cookies/test", timeout=15)
        return resp.json()


chartshot_client = ChartShotClient()
