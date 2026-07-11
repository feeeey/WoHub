"""跨测试文件共享的搭建函数。"""


def save_config_with_channel(**overrides) -> int:
    """建一条渠道并把 agent_config 指向它。overrides 直接透传 save_config
    （如 vision_model="v", enabled=True）。返回 channel id。"""
    from agent.config import save_channel, save_config
    cid = save_channel({"name": overrides.pop("channel_name", "test-ch"),
                        "provider": overrides.pop("channel_provider", "openai"),
                        "base_url": overrides.pop("channel_base_url", ""),
                        "api_key": overrides.pop("channel_api_key", "k")})
    save_config({"channel_id": cid, "model": "m", "enabled": True, **overrides})
    return cid
