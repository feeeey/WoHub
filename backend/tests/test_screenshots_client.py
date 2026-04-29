import pytest
from unittest.mock import patch, MagicMock
from screenshots.client import ChartShotClient


@pytest.fixture
def csc():
    return ChartShotClient("http://fake-chartshot:5000")


def test_health_check(csc):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": "ok", "service": "chartshot"}
    mock_resp.raise_for_status = MagicMock()

    with patch("screenshots.client.requests.get", return_value=mock_resp):
        result = csc.health()
        assert result["status"] == "ok"


def test_screenshot_success(csc):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "ok": True,
        "files": ["BTC_1h_20260406.png"],
        "paths": ["/app/output/BTC_1h_20260406.png"],
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("screenshots.client.requests.post", return_value=mock_resp):
        result = csc.screenshot("BTCUSDT", ["1h"])
        assert result["ok"] is True
        assert len(result["files"]) == 1


def test_screenshot_error(csc):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"ok": False, "error": "Timeout"}
    mock_resp.status_code = 504
    mock_resp.raise_for_status.side_effect = Exception("504")

    with patch("screenshots.client.requests.post", return_value=mock_resp):
        result = csc.screenshot("BTCUSDT", ["1h"])
        assert result["ok"] is False


def test_get_screenshot_url(csc):
    url = csc.screenshot_url("BTC_1h_20260406.png")
    assert url == "http://fake-chartshot:5000/api/screenshot/file/BTC_1h_20260406.png"
