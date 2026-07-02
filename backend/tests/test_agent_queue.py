# backend/tests/test_agent_queue.py
import json
from agent.queue import enqueue_run, claim_next, finish_run, fail_run, get_run


def test_enqueue_claim_finish_cycle():
    rid = enqueue_run(task_id=None, context={"task_id": 1, "candidates": []})
    row = claim_next()
    assert row["id"] == rid and row["status"] == "running"
    assert json.loads(row["context_json"])["task_id"] == 1
    assert claim_next() is None                      # 队列已空
    finish_run(rid, model="m", prompt_version="v1", trace={"steps": []},
               input_tokens=10, output_tokens=5)
    assert get_run(rid)["status"] == "done"


def test_fail_run_records_error():
    rid = enqueue_run(task_id=None, context={})
    claim_next()
    fail_run(rid, "boom")
    row = get_run(rid)
    assert row["status"] == "failed" and row["error"] == "boom"


def test_finish_run_oversized_trace_stays_valid_json():
    rid = enqueue_run(task_id=None, context={})
    claim_next()
    huge = {"steps": ["x" * 1000] * 300, "reused": [7]}   # >200k chars serialized
    finish_run(rid, model="m", prompt_version="v1", trace=huge,
               input_tokens=0, output_tokens=0)
    row = get_run(rid)
    parsed = json.loads(row["trace_json"])   # 不抛 JSONDecodeError
    assert parsed["_truncated"] is True and parsed["reused"] == [7]
