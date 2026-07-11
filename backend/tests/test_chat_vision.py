import os
import pytest
from unittest.mock import patch
from pydantic_ai.models.test import TestModel
from agent.config import load_config
from agent.chat import store, vision, runtime
from tests.helpers import save_config_with_channel

PNG_1PX = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
           b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
           b"\xff?\x00\x05\xfe\x02\xfe\xdc\xccY\xe7\x00\x00\x00\x00IEND\xaeB`\x82")


def test_config_roundtrips_vision_model():
    save_config_with_channel(vision_model="gemini-vision")
    assert load_config().vision_model == "gemini-vision"


def test_describe_image_uses_vision_model_slot():
    save_config_with_channel(vision_model="v")
    cfg = load_config()
    with patch("agent.chat.vision.build_model",
               return_value=TestModel(call_tools=[], custom_output_text="上升趋势，MACD 金叉")) as bm:
        out = vision.describe_image(cfg, PNG_1PX, "image/png")
    assert "上升趋势" in out
    assert bm.call_args.args[1] == "v"               # build_model(channel, model_name)
    assert bm.call_args.args[0].id == cfg.vision_channel.id


def test_load_image_reads_upload_dir(tmp_path):
    from config import settings
    with patch.object(settings, "chat_uploads_dir", str(tmp_path)):
        (tmp_path / "a.png").write_bytes(PNG_1PX)
        data, mt = vision.load_image("upload", "a.png")
    assert data == PNG_1PX and mt == "image/png"
    with pytest.raises(FileNotFoundError):
        vision.load_image("upload", "nope.png")


def _turn_with_image(tmp_path):
    from config import settings
    settings.chat_uploads_dir = str(tmp_path)          # 测试内直接指向 tmp
    (tmp_path / "b.png").write_bytes(PNG_1PX)
    sid = store.create_session()
    mid = store.add_message(sid, "user", "看这张图",
                            images=[{"kind": "upload", "filename": "b.png"}])
    store.create_turn(sid, mid)
    return sid, store.claim_next_turn()


def test_runtime_relays_image_when_vision_configured(tmp_path):
    save_config_with_channel(vision_model="v")
    sid, row = _turn_with_image(tmp_path)
    with patch("agent.chat.runtime.describe_image",
               return_value="图为BTC 4h，回踩EMA55") as di:
        runtime.run_turn(row, model_override=TestModel(call_tools=[], custom_output_text="分析如下"))
    assert di.called
    # 中继描述进入了 trace（视觉调用作为一步记录）
    steps = store.list_messages(sid)[-1]["trace"]["steps"]
    assert any(s.get("tool") == "vision_relay" for s in steps)


def test_runtime_passes_binary_when_no_vision_model(tmp_path):
    save_config_with_channel(vision_model="")
    sid, row = _turn_with_image(tmp_path)
    runtime.run_turn(row, model_override=TestModel(call_tools=[], custom_output_text="ok"))
    # 直传路径：不调用中继，轮次正常完成（TestModel 接受多模态输入）
    assert store.list_messages(sid)[-1]["error"] is None
