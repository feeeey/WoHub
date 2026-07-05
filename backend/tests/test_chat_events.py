from agent.chat import store, events


def _turn():
    sid = store.create_session()
    return sid, store.create_turn(sid, store.add_message(sid, "user", "x"))


def test_append_and_seq_monotonic():
    sid, tid = _turn()
    e1 = events.append_event(tid, "text_delta", {"text": "a"})
    e2 = events.append_event(tid, "tool_start", {"tool": "get_klines", "args": {}})
    rows = events.turn_events(tid)
    assert [r["seq"] for r in rows] == [1, 2]
    assert rows[0]["payload"] == {"text": "a"}
    assert e2 > e1


def test_events_after_resume_no_dup_no_loss():
    sid, tid = _turn()
    ids = [events.append_event(tid, "text_delta", {"text": str(i)}) for i in range(5)]
    part1 = events.events_after(sid, 0, limit=2)
    part2 = events.events_after(sid, part1[-1]["id"])
    got = [r["id"] for r in part1 + part2]
    assert got == ids                      # 不重不漏、按 id 升序


def test_events_after_scoped_to_session():
    sid1, tid1 = _turn()
    sid2, tid2 = _turn()
    events.append_event(tid1, "text_delta", {"text": "s1"})
    events.append_event(tid2, "text_delta", {"text": "s2"})
    assert all(r["turn_id"] == tid2 for r in events.events_after(sid2, 0))


def test_last_event_id_empty_is_zero():
    assert events.last_event_id() == 0


def test_delete_session_cascades_events():
    sid, tid = _turn()
    events.append_event(tid, "text_delta", {"text": "a"})
    store.delete_session(sid)
    assert events.events_after(sid, 0) == []
