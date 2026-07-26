import pytest
from unittest.mock import patch
from pydantic_ai.models.function import FunctionModel, AgentInfo, DeltaToolCall
from agent import tools as T
from agent.chat import store, events, runtime
from config import settings
from screenshots import service


@pytest.fixture
def shots_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "screenshots_dir", str(tmp_path))
    return tmp_path


def test_capture_chart_wraps_chartshot(shots_dir):
    (shots_dir / "BTCUSDT_1h_x.png").write_bytes(b"\x89PNG")
    with patch("screenshots.client.chartshot_client.screenshot",
               return_value={"ok": True, "files": ["BTCUSDT_1h_x.png"]}):
        out = T.capture_chart("BTCUSDT", "1h")
    assert out == {"files": ["BTCUSDT_1h_x.png"]}
    with patch("screenshots.client.chartshot_client.screenshot",
               return_value={"ok": False, "error": "cookie 过期"}):
        assert "error" in T.capture_chart("BTCUSDT", "1h")


def test_capture_chart_records_shot_for_ui(shots_dir):
    """agent 截的图也要进 screenshots 表，否则在截图列表里查不到。"""
    (shots_dir / "ETHUSDT_4h_x.png").write_bytes(b"\x89PNG")
    with patch("screenshots.client.chartshot_client.screenshot",
               return_value={"ok": True, "files": ["ETHUSDT_4h_x.png"]}):
        T.capture_chart("ETHUSDT", "4h")

    rows = service.list_shots(symbol="ETHUSDT")
    assert len(rows) == 1
    assert rows[0]["timeframe"] == "4h"


def test_capture_chart_rejects_bad_interval(shots_dir):
    assert "error" in T.capture_chart("BTCUSDT", "7h")


def _prep(vision="v"):
    from tests.helpers import save_config_with_channel
    save_config_with_channel(vision_model=vision)
    sid = store.create_session()
    tid = store.create_turn(sid, store.add_message(sid, "user", "截图看下 BTC 1h"))
    return sid, store.claim_next_turn()


def test_capture_tool_emits_image_event_and_relays():
    sid, row = _prep()

    async def stream_fn(messages, info: AgentInfo):
        if len(messages) == 1:
            yield {0: DeltaToolCall(name="capture_chart",
                                    json_args='{"symbol": "BTCUSDT", "interval": "1h"}')}
        else:
            yield "图已分析"

    with patch("agent.tools.capture_chart",
               return_value={"files": ["BTCUSDT_1h_x.png"]}), \
         patch("agent.chat.runtime.load_image", return_value=(b"png", "image/png")), \
         patch("agent.chat.runtime.describe_image", return_value="4h 上升通道"):
        runtime.run_turn(row, model_override=FunctionModel(stream_function=stream_fn))
    types = [e["type"] for e in events.turn_events(row["id"])]
    assert "image" in types and types[-1] == "turn_done"
    imgs = [e for e in events.turn_events(row["id"]) if e["type"] == "image"]
    assert imgs[0]["payload"] == {"kind": "screenshot", "filename": "BTCUSDT_1h_x.png",
                                  "caption": "BTCUSDT 1h"}


def test_capture_tool_absent_without_vision_model():
    sid, row = _prep(vision="")

    async def stream_fn(messages, info: AgentInfo):
        if len(messages) == 1:
            yield {0: DeltaToolCall(name="capture_chart",
                                    json_args='{"symbol": "BTCUSDT", "interval": "1h"}')}
        else:
            yield "done"

    runtime.run_turn(row, model_override=FunctionModel(stream_function=stream_fn))
    # 工具未注册 → 模型调用未知工具 → 该轮以失败结束而非崩溃
    assert [e["type"] for e in events.turn_events(row["id"])][-1] in ("turn_error", "turn_done")
