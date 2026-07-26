from fastapi import APIRouter, HTTPException

from evals import service

router = APIRouter(prefix="/evals")


@router.post("/live")
def start_live():
    """启动金标实跑（后台子进程，产生真实 LLM 费用）。轮询 GET /runs/{id} 看进度。"""
    run_id, reason = service.start_live_run()
    if run_id is None:
        raise HTTPException(409, reason)
    return {"run_id": run_id}


@router.post("/offline")
def run_offline():
    """存量轨迹离线报告（零 LLM 调用，同步返回）。"""
    return service.run_offline()


@router.get("/runs")
def list_runs(limit: int = 20):
    return {"runs": service.list_runs(limit)}


@router.get("/runs/{run_id}")
def get_run(run_id: int):
    run = service.get_run(run_id)
    if not run:
        raise HTTPException(404, "评测记录不存在")
    return run


@router.delete("/runs/{run_id}")
def delete_run(run_id: int):
    if not service.delete_run(run_id):
        raise HTTPException(404, "评测记录不存在或仍在运行")
    return {"ok": True}
