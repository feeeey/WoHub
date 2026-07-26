"""任务流水线用的截图入口 —— service + dispatch 的薄封装。

保留 capture_and_dispatch 这个名字和签名是为了 executor 的四个调用点不必改写；
实际逻辑已经下沉到 screenshots.service（截图+落库）和 screenshots.dispatch（推送）。
"""

from app_logger import log as applog
from screenshots import dispatch, service


def capture_and_dispatch(task_id, symbol, timeframes, channel=None):
    """截图并（若给了渠道）推送。返回 service.capture 的结果 dict。

    channel 为 None 时只截图存档 —— 这是有意支持的：任务没配推送渠道
    不该导致完全不截图。
    """
    try:
        # source="task"：定时任务享有 ChartShot 队列优先权，手动截图会为它让路
        result = service.capture(symbol, timeframes, task_id=task_id, source="task")
    except ValueError as e:
        applog("screenshots", "error", f"截图参数非法 {symbol} {timeframes}: {e}")
        return {"ok": False, "symbol": symbol, "timeframes": list(timeframes or []),
                "shots": [], "errors": [str(e)], "pushes": []}

    if channel and result["shots"]:
        result["pushes"] = dispatch.push_shots(
            result["shots"], [channel], task_id=task_id)
    else:
        result["pushes"] = []
    return result


def get_screenshot_for_signal(signal_id):
    """该信号最近一张截图的绝对路径，无则 None。"""
    return service.get_screenshot_for_signal(signal_id)
