"""评测运行管理测试。live 的子进程用 mock 替掉——测的是运行生命周期，
不是 CLI 本身（CLI 由 test_evals_runner 覆盖）。"""
import json
from unittest.mock import patch

import pytest

from agent.chat import store
from evals import service


@pytest.fixture(autouse=True)
def _reset_active():
    with service._active_lock:
        service._active_run_id = None
    yield
    with service._active_lock:
        service._active_run_id = None


def _fake_subprocess_ok(payload):
    """替身 subprocess.run：把 payload 写进 --json 指定的文件并返回 0。"""
    class R:
        returncode = 0
        stderr = ""
        stdout = ""

    def fake(cmd, **kw):
        out_path = cmd[cmd.index("--json") + 1]
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        return R()
    return fake


# ---- offline ----

@pytest.mark.asyncio
async def test_offline_run_returns_buckets(client):
    sid = store.create_session()
    store.add_message(sid, "user", "看下 BTC")
    store.add_message(sid, "assistant",
                      "结论：BTCUSDT 短线偏多，RSI 57.3，MACD 金叉，结构完好未破位。",
                      trace={"prompt_version": "chat-v1", "steps": []}, model="m1")

    resp = await client.post("/api/evals/offline")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "done" and data["kind"] == "offline"
    assert data["summary"]["n_traces"] == 1
    assert data["summary"]["buckets"][0]["prompt_version"] == "chat-v1"


@pytest.mark.asyncio
async def test_offline_empty_db_still_done(client):
    resp = await client.post("/api/evals/offline")
    assert resp.status_code == 200
    assert resp.json()["summary"]["n_traces"] == 0


# ---- live 生命周期（子进程 mock，同步执行 _execute_live）----

def test_live_run_lifecycle_success():
    from tests.helpers import save_config_with_channel
    save_config_with_channel()
    payload = {"prompt_version": "chat-v1", "model": "m",
               "summary": {"n_cases": 12, "avg_total": 0.98},
               "results": [{"case_id": "x", "total": 1.0}]}

    with patch("evals.service.subprocess.run", side_effect=_fake_subprocess_ok(payload)):
        run_id, reason = service.start_live_run()
        assert run_id and reason is None
        # 测试里同步等线程收尾
        import threading
        for t in threading.enumerate():
            if t.name == f"eval-live-{run_id}":
                t.join(timeout=10)

    run = service.get_run(run_id)
    assert run["status"] == "done"
    assert run["summary"]["avg_total"] == 0.98
    assert run["results"][0]["case_id"] == "x"
    with service._active_lock:
        assert service._active_run_id is None    # 闸门已释放


def test_live_run_requires_channel():
    run_id, reason = service.start_live_run()
    assert run_id is None and "未配置" in reason


def test_live_run_concurrency_guard():
    from tests.helpers import save_config_with_channel
    save_config_with_channel()
    with service._active_lock:
        service._active_run_id = 42
    run_id, reason = service.start_live_run()
    assert run_id is None and "#42" in reason


def test_live_run_subprocess_failure_recorded():
    from tests.helpers import save_config_with_channel
    save_config_with_channel()

    class R:
        returncode = 2
        stderr = "未配置 LLM 渠道"
        stdout = ""

    with patch("evals.service.subprocess.run", return_value=R()):
        run_id, _ = service.start_live_run()
        import threading
        for t in threading.enumerate():
            if t.name == f"eval-live-{run_id}":
                t.join(timeout=10)

    run = service.get_run(run_id)
    assert run["status"] == "failed"
    assert "exit 2" in run["error"]


def test_stale_running_rows_marked_failed_on_list():
    """服务重启遗留的 running 行：list 时判 failed，避免前端永远转圈。"""
    from config import settings
    from database import get_db
    db = get_db(settings.db_path)
    db.execute("INSERT INTO eval_runs (kind, status) VALUES ('live', 'running')")
    db.commit()
    db.close()

    runs = service.list_runs()
    assert runs[0]["status"] == "failed"
    assert "重启" in runs[0]["error"]


# ---- REST ----

@pytest.mark.asyncio
async def test_runs_list_and_detail_and_delete(client):
    await client.post("/api/evals/offline")
    resp = await client.get("/api/evals/runs")
    runs = resp.json()["runs"]
    assert len(runs) == 1
    rid = runs[0]["id"]

    detail = await client.get(f"/api/evals/runs/{rid}")
    assert detail.status_code == 200
    assert "results" in detail.json()

    assert (await client.delete(f"/api/evals/runs/{rid}")).status_code == 200
    assert (await client.get(f"/api/evals/runs/{rid}")).status_code == 404
    assert (await client.delete("/api/evals/runs/999")).status_code == 404


@pytest.mark.asyncio
async def test_live_endpoint_409_when_busy(client):
    from tests.helpers import save_config_with_channel
    save_config_with_channel()
    with service._active_lock:
        service._active_run_id = 7
    resp = await client.post("/api/evals/live")
    assert resp.status_code == 409


@pytest.mark.asyncio
@pytest.mark.no_auth_override
async def test_evals_endpoints_require_auth(client):
    for method, url in [("GET", "/api/evals/runs"),
                        ("POST", "/api/evals/offline"),
                        ("POST", "/api/evals/live")]:
        resp = await client.request(method, url)
        assert resp.status_code == 401, f"{method} {url} 未受鉴权保护"
