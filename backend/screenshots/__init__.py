from screenshots.client import ChartShotClient, chartshot_client
from screenshots.service import (
    capture,
    delete_shot,
    get_shot,
    get_screenshot_for_signal,
    list_shots,
    normalize_symbol,
    normalize_timeframes,
)
from screenshots.dispatch import capture_and_push, push_shots, resolve_channels
from screenshots.pipeline import capture_and_dispatch

__all__ = [
    "ChartShotClient",
    "chartshot_client",
    # 截图 + 落库
    "capture",
    "list_shots",
    "get_shot",
    "delete_shot",
    "get_screenshot_for_signal",
    "normalize_symbol",
    "normalize_timeframes",
    # 推送
    "capture_and_push",
    "push_shots",
    "resolve_channels",
    # 任务流水线兼容入口
    "capture_and_dispatch",
]
