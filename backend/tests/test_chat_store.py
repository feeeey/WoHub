from agent.chat import store


def test_session_crud_and_title():
    sid = store.create_session()
    assert store.list_sessions()[0]["title"] == "新会话"
    store.rename_session(sid, "BTC 结构分析")
    assert store.list_sessions()[0]["title"] == "BTC 结构分析"
    store.delete_session(sid)
    assert store.list_sessions() == []


def test_message_roundtrip_parses_json_fields():
    sid = store.create_session()
    mid = store.add_message(sid, "user", "看下 BTC",
                            images=[{"kind": "upload", "filename": "a.png"}])
    store.add_message(sid, "assistant", "好的", trace={"steps": [1]},
                      model="m", input_tokens=10, output_tokens=5)
    msgs = store.list_messages(sid)
    assert msgs[0]["id"] == mid and msgs[0]["images"] == [{"kind": "upload", "filename": "a.png"}]
    assert msgs[1]["trace"] == {"steps": [1]} and msgs[1]["output_tokens"] == 5


def test_turn_queue_claim_and_finish():
    sid = store.create_session()
    mid = store.add_message(sid, "user", "hi")
    tid = store.create_turn(sid, mid)
    row = store.claim_next_turn()
    assert row["id"] == tid and row["status"] == "running"
    assert store.claim_next_turn() is None
    store.finish_turn(tid, "done")
    assert store.active_turn(sid) is None


def test_cancel_flag():
    sid = store.create_session()
    tid = store.create_turn(sid, store.add_message(sid, "user", "x"))
    assert store.cancel_requested(tid) is False
    assert store.request_cancel(tid) is True
    assert store.cancel_requested(tid) is True


def test_recover_interrupted_marks_running_failed():
    sid = store.create_session()
    tid = store.create_turn(sid, store.add_message(sid, "user", "x"))
    store.claim_next_turn()
    assert store.recover_interrupted() == [{"id": tid, "session_id": sid}]
    assert store.active_turn(sid) is None      # failed 不算 active
