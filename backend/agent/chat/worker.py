"""Chat worker: single daemon thread draining chat_turns. Never runs in the
APScheduler pool; own short-lived DB connections; clean shutdown."""
import sys
import threading
from app_logger import log as applog
from agent.chat import store, events

_stop = threading.Event()
_thread = None


def _process(turn_row):
    from agent.chat.runtime import run_turn
    run_turn(turn_row)


def _loop(interval):
    mod = sys.modules[__name__]
    while not _stop.wait(interval):
        try:
            row = store.claim_next_turn()
            if row is None:
                continue
            try:
                mod._process(row)                 # 模块属性调用，测试可 patch
            except Exception as e:                # run_turn 自兜底；这里是最后防线
                applog("chat", "error", f"turn #{row['id']} crashed: {e!r}")
                events.append_event(row["id"], "turn_error", {"error": str(e)[:500]})
                store.finish_turn(row["id"], "failed")
                try:
                    store.add_message(row["session_id"], "assistant", "",
                                      error=str(e)[:2000])
                except Exception:
                    pass                           # 兜底的兜底：绝不能把 worker 循环带崩
        except Exception as e:
            applog("chat", "error", f"worker loop: {e!r}")


def start_worker(interval=0.5):
    global _thread
    if _thread and _thread.is_alive():
        return
    # 启动恢复：running→failed（补事件 + assistant 错误消息，前端才有重试按钮），
    # queued 保留由循环自然续跑
    for t in store.recover_interrupted():
        events.append_event(t["id"], "turn_error", {"error": "服务重启中断，可重试"})
        store.add_message(t["session_id"], "assistant", "",
                          error="服务重启中断，可重试")
    from agent.chat.semantics import seed_defaults
    seed_defaults()
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(interval,), daemon=True,
                               name="chat-worker")
    _thread.start()


def stop_worker():
    global _thread
    _stop.set()
    if _thread:
        _thread.join(timeout=10)
        if _thread.is_alive():
            applog("chat", "warn", "chat worker did not stop within 10s")
        else:
            _thread = None
