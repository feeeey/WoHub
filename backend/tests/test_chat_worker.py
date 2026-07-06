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


def test_worker_seed_semantics_on_start():
    from agent.chat import semantics
    worker.start_worker(interval=0.05)
    worker.stop_worker()
    assert len(semantics.get_all()) == 8


def test_stop_worker_joins():
    worker.start_worker(interval=0.05)
    worker.stop_worker()
    assert worker._thread is None or not worker._thread.is_alive()
