import time
from unittest.mock import patch
from agent.chat import store, worker, events


def test_worker_drains_queue_and_recovers():
    sid = store.create_session()
    # 制造一个"上次运行被打断"的 running turn
    t_stale = store.create_turn(sid, store.add_message(sid, "user", "old"))
    store.claim_next_turn()
    # 再排一个正常 queued turn
    t_new = store.create_turn(sid, store.add_message(sid, "user", "new"))

    done = []
    with patch.object(worker, "_process", side_effect=lambda row: done.append(row["id"])):
        worker.start_worker(interval=0.05)
        try:
            deadline = time.monotonic() + 3
            while not done and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            worker.stop_worker()
    assert done == [t_new]                       # 只处理 queued 的
    # stale running 被判 failed 且有事件
    assert [e["type"] for e in events.turn_events(t_stale)] == ["turn_error"]
    # 且写入可重试的 assistant 错误消息（前端悬空兜底，靠此驱动重试按钮）
    msgs = store.list_messages(sid)
    assert msgs[-1]["role"] == "assistant" and msgs[-1]["error"] == "服务重启中断，可重试"


def test_worker_crash_backstop_writes_assistant_error():
    sid = store.create_session()
    store.create_turn(sid, store.add_message(sid, "user", "hi"))

    with patch.object(worker, "_process", side_effect=RuntimeError("boom")):
        worker.start_worker(interval=0.05)
        try:
            deadline = time.monotonic() + 3
            while store.active_turn(sid) is not None and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            worker.stop_worker()

    msgs = store.list_messages(sid)
    assert msgs[-1]["role"] == "assistant" and msgs[-1]["error"] == "boom"


def test_worker_seed_semantics_on_start():
    from agent.chat import semantics
    worker.start_worker(interval=0.05)
    worker.stop_worker()
    assert len(semantics.get_all()) == 8


def test_stop_worker_joins():
    worker.start_worker(interval=0.05)
    t = worker._thread
    worker.stop_worker()
    assert t is not None and not t.is_alive()   # 线程真的停了
    assert worker._thread is None               # 句柄已清空，可安全重启
