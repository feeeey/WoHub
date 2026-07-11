from agent.config import (save_channel, get_channel, list_channels, delete_channel,
                          channel_in_use, save_config, load_config)


def _mk(name="OpenRouter", key="sk-1"):
    return save_channel({"name": name, "provider": "openai",
                         "base_url": "https://openrouter.ai/api/v1", "api_key": key})


def test_channel_crud_key_semantics():
    cid = _mk()
    assert get_channel(cid).api_key == "sk-1"
    save_channel({"id": cid, "name": "OpenRouter", "provider": "openai",
                  "base_url": "", "api_key": None})
    assert get_channel(cid).api_key == "sk-1"       # None = 不改
    assert get_channel(cid).base_url == ""          # 其他字段照常更新
    save_channel({"id": cid, "name": "OpenRouter", "provider": "openai",
                  "base_url": "", "api_key": ""})
    assert get_channel(cid).api_key is None          # "" = 清除
    pub = list_channels()[0]
    assert "api_key" not in pub and "api_key_enc" not in pub
    assert pub["has_api_key"] is False and pub["name"] == "OpenRouter"


def test_get_channel_missing_returns_none():
    assert get_channel(999) is None


def test_load_config_resolves_channels_and_vision_fallback():
    cid = _mk()
    save_config({"channel_id": cid, "model": "m1", "vision_model": "vm", "enabled": True})
    cfg = load_config()
    assert cfg.main_channel.id == cid and cfg.main_channel.api_key == "sk-1"
    assert cfg.vision_channel.id == cid              # vision_channel_id NULL → 跟随主渠道

    cid2 = _mk(name="DashScope", key="sk-2")
    save_config({"vision_channel_id": cid2})
    cfg = load_config()
    assert cfg.vision_channel.id == cid2 and cfg.main_channel.id == cid


def test_load_config_broken_ref_gives_none():
    save_config({"channel_id": 999, "vision_channel_id": 998, "model": "m"})
    cfg = load_config()
    assert cfg.main_channel is None and cfg.vision_channel is None


def test_channel_in_use_and_delete():
    cid = _mk()
    assert channel_in_use(cid) is False
    save_config({"channel_id": cid})
    assert channel_in_use(cid) is True
    save_config({"channel_id": None, "vision_channel_id": cid})
    assert channel_in_use(cid) is True
    save_config({"vision_channel_id": None})
    assert channel_in_use(cid) is False
    delete_channel(cid)
    assert get_channel(cid) is None
