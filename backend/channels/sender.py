import json
from channels.telegram import TelegramChannel


def create_channel(channel_type: str, config: dict):
    if channel_type == "telegram":
        return TelegramChannel(
            bot_token=config.get("bot_token", ""),
            chat_id=config.get("chat_id", ""),
        )
    raise ValueError(f"Unsupported channel type: {channel_type}")


def send_text(channel_type: str, config: dict, text: str) -> int:
    ch = create_channel(channel_type, config)
    return ch.send_text(text)


def send_photo(channel_type: str, config: dict, photo_path: str, caption: str = "") -> int:
    ch = create_channel(channel_type, config)
    return ch.send_photo(photo_path, caption)


def test_channel(channel_type: str, config: dict) -> dict:
    ch = create_channel(channel_type, config)
    return ch.test_connection()
