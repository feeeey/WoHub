import pytest
from unittest.mock import patch, MagicMock
from channels.telegram import TelegramChannel


@pytest.fixture
def tg():
    return TelegramChannel(bot_token="fake-token", chat_id="-100123")


def _mock_response(ok=True, message_id=42):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "ok": ok,
        "result": {"message_id": message_id},
    }
    resp.raise_for_status = MagicMock()
    return resp


def test_send_text(tg):
    with patch("channels.telegram.httpx.post", return_value=_mock_response()) as mock:
        msg_id = tg.send_text("Hello")
        assert msg_id == 42
        call_args = mock.call_args
        assert "/sendMessage" in call_args[0][0]
        body = call_args[1]["json"]
        assert body["text"] == "Hello"
        assert body["chat_id"] == "-100123"
        assert body["parse_mode"] == "HTML"


def test_send_text_custom_chat(tg):
    with patch("channels.telegram.httpx.post", return_value=_mock_response()):
        msg_id = tg.send_text("Test", chat_id="-999")
        assert msg_id == 42


def test_send_text_failure(tg):
    resp = _mock_response(ok=False)
    resp.json.return_value = {"ok": False, "description": "Bad Request"}
    with patch("channels.telegram.httpx.post", return_value=resp):
        with pytest.raises(RuntimeError, match="Telegram"):
            tg.send_text("fail")


def test_send_photo(tg):
    mock_resp = _mock_response()
    with patch("channels.telegram.httpx.post", return_value=mock_resp) as mock:
        with patch("builtins.open", MagicMock()):
            msg_id = tg.send_photo("/fake/path.png", caption="Test")
            assert msg_id == 42
            assert "/sendPhoto" in mock.call_args[0][0]


def test_test_connection(tg):
    with patch("channels.telegram.httpx.get") as mock:
        mock.return_value = MagicMock(
            json=MagicMock(return_value={
                "ok": True,
                "result": {"username": "test_bot", "first_name": "Test"},
            })
        )
        result = tg.test_connection()
        assert result["ok"] is True
        assert result["bot_name"] == "Test"
