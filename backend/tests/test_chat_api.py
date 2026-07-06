import asyncio
import os
import pytest
from agent.chat import store, events

PNG_1PX = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
           b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
           b"\xff?\x00\x05\xfe\x02\xfe\xdc\xccY\xe7\x00\x00\x00\x00IEND\xaeB`\x82")


@pytest.mark.asyncio
async def test_session_crud(client):
    async with client as c:
        sid = (await c.post("/api/chat/sessions", json={})).json()["id"]
        assert (await c.get("/api/chat/sessions")).json()[0]["id"] == sid
        r = await c.patch(f"/api/chat/sessions/{sid}", json={"title": "改名"})
        assert r.status_code == 200
        assert (await c.get("/api/chat/sessions")).json()[0]["title"] == "改名"
        assert (await c.delete(f"/api/chat/sessions/{sid}")).status_code == 200
        assert (await c.get("/api/chat/sessions")).json() == []


@pytest.mark.asyncio
async def test_post_message_creates_turn_and_409_when_active(client):
    async with client as c:
        sid = (await c.post("/api/chat/sessions", json={})).json()["id"]
        r = (await c.post(f"/api/chat/sessions/{sid}/messages",
                          data={"content": "看下 BTC"})).json()
        assert r["turn_id"] and r["message_id"]
        # 上一轮还 queued，再发 409
        r2 = await c.post(f"/api/chat/sessions/{sid}/messages", data={"content": "再问"})
        assert r2.status_code == 409


@pytest.mark.asyncio
async def test_upload_validation_and_image_serving(client):
    async with client as c:
        sid = (await c.post("/api/chat/sessions", json={})).json()["id"]
        r = await c.post(f"/api/chat/sessions/{sid}/messages",
                         data={"content": "看图"},
                         files={"files": ("k.png", PNG_1PX, "image/png")})
        assert r.status_code == 200
        img = (await c.get(f"/api/chat/sessions/{sid}/messages")).json()[
            "messages"][0]["images"][0]
        assert img["kind"] == "upload"
        got = await c.get(f"/api/chat/images/upload/{img['filename']}")
        assert got.status_code == 200 and got.content == PNG_1PX
        # 类型校验
        bad = await c.post(f"/api/chat/sessions/{sid}/messages",
                           data={"content": "x"},
                           files={"files": ("a.txt", b"hi", "text/plain")})
        assert bad.status_code == 415
        # 路径穿越
        assert (await c.get("/api/chat/images/upload/..%2Fwohub.db")).status_code in (400, 404)


@pytest.mark.asyncio
async def test_messages_returns_active_turn_and_events(client):
    async with client as c:
        sid = (await c.post("/api/chat/sessions", json={})).json()["id"]
        r = (await c.post(f"/api/chat/sessions/{sid}/messages",
                          data={"content": "hi"})).json()
        events.append_event(r["turn_id"], "text_delta", {"text": "思考中"})
        out = (await c.get(f"/api/chat/sessions/{sid}/messages")).json()
        assert out["active_turn"]["id"] == r["turn_id"]
        assert out["active_events"][0]["payload"]["text"] == "思考中"
        assert out["last_event_id"] >= out["active_events"][-1]["id"]


@pytest.mark.asyncio
async def test_cancel_endpoint(client):
    async with client as c:
        sid = (await c.post("/api/chat/sessions", json={})).json()["id"]
        tid = (await c.post(f"/api/chat/sessions/{sid}/messages",
                            data={"content": "hi"})).json()["turn_id"]
        assert (await c.post(f"/api/chat/turns/{tid}/cancel")).json()["ok"] is True
        assert store.cancel_requested(tid)


@pytest.mark.asyncio
async def test_sse_stream_replays_backlog(client):
    async with client as c:
        sid = (await c.post("/api/chat/sessions", json={})).json()["id"]
        tid = (await c.post(f"/api/chat/sessions/{sid}/messages",
                            data={"content": "hi"})).json()["turn_id"]
        e1 = events.append_event(tid, "text_delta", {"text": "a"})
        lines = []
        async with c.stream("GET", f"/api/chat/sessions/{sid}/stream?after=0") as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            async for line in r.aiter_lines():
                lines.append(line)
                if line.startswith("data:"):
                    break
        assert any(l == f"id: {e1}" for l in lines)
        assert any(l.startswith("event: text_delta") for l in lines)
