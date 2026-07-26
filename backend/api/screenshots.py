import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional

from config import settings
from database import get_db
from screenshots import dispatch, service
from screenshots.client import chartshot_client

router = APIRouter(prefix="/screenshots")


def _task_exists(task_id: int) -> bool:
    db = get_db(settings.db_path)
    try:
        return db.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone() is not None
    finally:
        db.close()


class CaptureBody(BaseModel):
    symbol: str
    timeframes: Optional[List[str]] = None
    channel_ids: Optional[List[int]] = None
    caption: Optional[str] = None
    task_id: Optional[int] = None


class PushBody(BaseModel):
    channel_ids: List[int]
    caption: Optional[str] = None


# --- 具体路径必须注册在 /{shot_id} 之前：shot_id 是 int，
# --- 否则 /screenshots/cookies 会先撞上路径参数并以 422 收场。

@router.get("/status")
def chartshot_status():
    """Check ChartShot service health."""
    try:
        result = chartshot_client.health()
        return {"ok": True, "status": result.get("status", "unknown")}
    except Exception as e:
        return {"ok": False, "status": "unreachable", "error": str(e)}


@router.get("/cookies")
def get_chartshot_cookies():
    """Get ChartShot cookies status."""
    try:
        result = chartshot_client.get_cookies()
        if result.get("ok"):
            raw = result.get("cookies", "")
            has = bool(raw.strip())
            display = raw[:80] + "..." if len(raw) > 80 else raw
            return {"ok": True, "has_cookies": has, "cookies_display": display}
        return {"ok": False, "error": result.get("error", "")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.put("/cookies")
def update_chartshot_cookies(body: dict):
    """Update ChartShot cookies."""
    raw = body.get("cookies", "")
    if not raw:
        return {"ok": False, "error": "cookies string required"}
    try:
        result = chartshot_client.update_cookies(raw)
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/cookies/test")
def test_chartshot_cookies():
    """Test ChartShot cookies validity."""
    try:
        return chartshot_client.test_cookies()
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/timeframes")
def list_timeframes():
    """可用周期，给前端下拉框用。"""
    return {"timeframes": list(service.VALID_TIMEFRAMES),
            "default": list(service.DEFAULT_TIMEFRAMES)}


@router.post("/capture")
def capture(body: CaptureBody):
    """截图，可选同步推送到指定渠道。

    同步阻塞：ChartShot 单 worker 串行渲染，多周期可能耗时数十秒到 2 分钟。
    截图失败返回 200 + ok:false（业务失败），与本路由其余端点风格一致；
    唯独「定时任务占用中」返回 409，让前端能把它和真失败区分开。
    """
    if body.task_id is not None and not _task_exists(body.task_id):
        # screenshots.task_id 有外键约束：悬空 id 会让落库失败，
        # 而那时截图已经拍完了 —— 提前拦下比事后报「已生成但未落库」好
        raise HTTPException(400, f"任务 {body.task_id} 不存在")
    try:
        result = dispatch.capture_and_push(
            body.symbol,
            body.timeframes,
            channel_ids=body.channel_ids,
            caption=body.caption,
            task_id=body.task_id,
            source="manual",
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    if result.get("busy"):
        raise HTTPException(409, result["errors"][0] if result["errors"]
                            else "定时任务截图进行中，请稍后再试")
    return result


@router.get("")
def list_screenshots(symbol: Optional[str] = None, task_id: Optional[int] = None,
                     timeframe: Optional[str] = None, limit: int = 50):
    """历史截图记录，倒序。"""
    return {"screenshots": service.list_shots(
        symbol=symbol, task_id=task_id, timeframe=timeframe, limit=limit)}


@router.get("/file/{filename}")
def get_screenshot_file(filename: str):
    """按文件名取图。文件名走白名单校验，再二次确认解析后的路径没逃出截图目录。"""
    if filename in (".", "..") or not service.FILENAME_RE.fullmatch(filename):
        raise HTTPException(400, "非法文件名")
    path = os.path.join(settings.screenshots_dir, filename)
    root = os.path.realpath(settings.screenshots_dir)
    if os.path.commonpath([os.path.realpath(path), root]) != root:
        raise HTTPException(400, "非法路径")
    if not os.path.isfile(path):
        raise HTTPException(404, "文件不存在")
    return FileResponse(path, media_type="image/png")


@router.post("/{shot_id}/push")
def push_screenshot(shot_id: int, body: PushBody):
    """把一张已存在的截图重新推送到指定渠道。"""
    shot = service.get_shot(shot_id)
    if not shot:
        raise HTTPException(404, "截图记录不存在")
    if not os.path.isfile(shot["file_path"]):
        raise HTTPException(410, "截图文件已不存在")

    channels, missing = dispatch.resolve_channels(body.channel_ids)
    if not channels:
        raise HTTPException(400, "没有可用的推送渠道（仅支持 telegram / discord）")

    pushes = dispatch.push_shots([shot], channels, caption=body.caption,
                                 task_id=shot["task_id"])
    return {"ok": all(p["ok"] for p in pushes), "pushes": pushes,
            "missing_channels": missing}


@router.delete("/{shot_id}")
def delete_screenshot(shot_id: int):
    """删除截图记录及其磁盘文件。"""
    if not service.delete_shot(shot_id):
        raise HTTPException(404, "截图记录不存在")
    return {"ok": True}
