import requests
from config import settings


class ChartShotClient:
    def __init__(self, base_url=None):
        self.base_url = (base_url or settings.chartshot_url).rstrip("/")

    def health(self):
        resp = requests.get(f"{self.base_url}/health", timeout=5)
        resp.raise_for_status()
        return resp.json()

    def screenshot(self, symbol, timeframes, timeout=None, source="manual"):
        # 必须比 ChartShot 自己的等待预算(30 + 100×周期数)更长，否则这边先断开，
        # 拿到的是连接错误而不是对面那条说明性的 504，worker 也仍在后台空转。
        if timeout is None:
            timeout = 45 + 110 * max(1, len(timeframes))
        try:
            resp = requests.post(
                f"{self.base_url}/api/screenshot",
                json={"symbol": symbol, "timeframes": timeframes, "source": source},
                timeout=timeout,
            )
            # 409 = 定时任务占用中，body 里带 busy 标记，照常解析
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
