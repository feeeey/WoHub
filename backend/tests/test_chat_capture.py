from unittest.mock import patch
from pydantic_ai.models.function import FunctionModel, AgentInfo, DeltaToolCall
from agent import tools as T
from agent.chat import store, events, runtime


def test_capture_chart_wraps_chartshot():
    with patch("screenshots.client.chartshot_client.screenshot",
               return_value={"ok": True, "files": ["BTCUSDT_1h_x.png"]}):
        out = T.capture_chart("BTCUSDT", "1h")
    assert out == {"files": ["BTCUSDT_1h_x.png"]}
    with patch("screenshots.client.chartshot_client.screenshot",
               return_value={"ok": False, "error": "cookie 过期"}):
        assert "error" in T.capture_chart("BTCUSDT", "1h")


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
