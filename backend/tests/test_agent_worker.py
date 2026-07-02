import time
from unittest.mock import patch


def test_worker_processes_queued_run(reset_db):
    from agent.queue import enqueue_run, get_run
    from agent import worker
    rid = enqueue_run(None, {"candidates": []})
    calls = []

    def fake_process(run_row):
        calls.append(run_row["id"])
        from agent.queue import finish_run
        finish_run(run_row["id"], model="x", prompt_version="v1", trace={},
                   input_tokens=0, output_tokens=0)

    with patch.object(worker, "_process_run", side_effect=fake_process):
        worker.start_worker(interval=0.05)
        for _ in range(100):
            if get_run(rid)["status"] == "done":
                break
            time.sleep(0.05)
        worker.stop_worker()
    assert calls == [rid]


def test_worker_marks_failed_on_crash(reset_db):
    from agent.queue import enqueue_run, get_run
    from agent import worker
    rid = enqueue_run(None, {"candidates": []})
    with patch.object(worker, "_process_run", side_effect=RuntimeError("boom")):
        worker.start_worker(interval=0.05)
        for _ in range(100):
            if get_run(rid)["status"] == "failed":
                break
            time.sleep(0.05)
        worker.stop_worker()
    assert get_run(rid)["status"] == "failed"
