import asyncio
import json
import os
import re
import time
import uuid
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from typing import Optional

from config import settings
from agent.chat import store, events

router = APIRouter(prefix="/chat")

MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg"}
_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class SessionBody(BaseModel):
    title: Optional[str] = None


@router.get("/sessions")
def list_sessions():
    return store.list_sessions()


@router.post("/sessions")
def create_session(body: SessionBody):
    return {"id": store.create_session(body.title)}


@router.patch("/sessions/{sid}")
def rename_session(sid: int, body: SessionBody):
    if not body.title or not body.title.strip():
        raise HTTPException(422, "标题不能为空")
    if not store.rename_session(sid, body.title.strip()):
        raise HTTPException(404, "会话不存在")
    return {"ok": True}


@router.delete("/sessions/{sid}")
def delete_session(sid: int):
    store.delete_session(sid)
    return {"ok": True}


@router.get("/sessions/{sid}/messages")
def get_messages(sid: int):
    msgs = store.list_messages(sid)
    active = store.active_turn(sid)
    return {"messages": msgs,
            "active_turn": active,
            "active_events": events.turn_events(active["id"]) if active else [],
            "last_event_id": events.last_event_id()}


def _save_upload(f: UploadFile) -> dict:
    ext = ALLOWED_IMAGE_TYPES.get(f.content_type)
    if not ext:
        raise HTTPException(415, f"仅支持 PNG/JPEG 图片，收到 {f.content_type}")
    data = f.file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "图片超过 5MB 上限")
    os.makedirs(settings.chat_uploads_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(settings.chat_uploads_dir, filename), "wb") as out:
        out.write(data)
    return {"kind": "upload", "filename": filename}


@router.post("/sessions/{sid}/messages")
def post_message(sid: int, content: str = Form(""),
                 files: list[UploadFile] = File(default=[])):
    if not any(s["id"] == sid for s in store.list_sessions()):
        raise HTTPException(404, "会话不存在")
    # Validate upload types before the active-turn conflict check: a
    # malformed request (unsupported file type) should surface as 415
    # regardless of session state, not get masked behind a 409.
    for f in files:
        if f.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(415, f"仅支持 PNG/JPEG 图片，收到 {f.content_type}")
    if store.active_turn(sid):
        raise HTTPException(409, "上一轮还在进行中（可先停止）")
    if not content.strip() and not files:
        raise HTTPException(422, "消息不能为空")
    images = [_save_upload(f) for f in files]
    mid = store.add_message(sid, "user", content.strip(), images=images or None)
    tid = store.create_turn(sid, mid)
    return {"turn_id": tid, "message_id": mid}


@router.post("/turns/{tid}/cancel")
def cancel_turn(tid: int):
    if not store.request_cancel(tid):
        raise HTTPException(404, "轮次不存在或已结束")
    return {"ok": True}


@router.get("/sessions/{sid}/stream")
async def stream(sid: int, after: int = 0, once: bool = False):
    async def gen():
        last = after
        last_beat = time.monotonic()
        while True:
            rows = events.events_after(sid, last)
            for r in rows:
                last = r["id"]
                payload = json.dumps(r["payload"], ensure_ascii=False)
                yield f"id: {r['id']}\nevent: {r['type']}\ndata: {payload}\n\n"
            if rows:
                last_beat = time.monotonic()
                continue
            if once:
                # 测试/调试逃生口：httpx ASGITransport 会缓冲整个 ASGI 响应，
                # 无限流在该环境下永不返回——once 模式追平积压即收流。
                # 生产 EventSource 不传 once；真实服务器在客户端断开时会取消本协程。
                return
            if time.monotonic() - last_beat > 15:
                yield ": ping\n\n"
                last_beat = time.monotonic()
            await asyncio.sleep(0.15)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.get("/images/{kind}/{filename}")
def get_image(kind: str, filename: str):
    dirs = {"upload": settings.chat_uploads_dir,
            "screenshot": settings.screenshots_dir}
    if kind not in dirs or not _FILENAME_RE.fullmatch(filename):
        raise HTTPException(400, "非法路径")
    path = os.path.join(dirs[kind], filename)
    if not os.path.isfile(path):
        raise HTTPException(404, "文件不存在")
    return FileResponse(path)
