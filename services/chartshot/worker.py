import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from config import (STALE_JOB_SECONDS, SUBPROC_KILL_GRACE,
                    CAPTURE_BASE_OVERHEAD, PER_TF_BUDGET)


@dataclass
class CaptureJob:
    symbol: str
    timeframes: list
    job_id: str = ""
    # "task" = 定时任务发起，享有优先权；其余（手动截图、agent）在有 task 工作时被拒
    source: str = "manual"
    result: Optional[list] = field(default=None, repr=False)
    error: Optional[str] = field(default=None, repr=False)
    done_event: threading.Event = field(default_factory=threading.Event, repr=False)


class CaptureWorker:
    def __init__(self):
        self._queue = queue.Queue()
        self._thread = None
        self._running = False
        # TradingView 同一 cookie 只允许一个图表登录端，所以 worker 必须单线程。
        # 定时任务不能被手动操作挤到队尾干等，因此手动 job 在有 task 工作时直接拒绝。
        self._lock = threading.Lock()
        self._task_inflight = 0   # 排队中 + 执行中的 task job 数
        self._active_since = None  # 当前 job 开始执行的时间戳

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print("[worker] Started")

    def stop(self):
        self._running = False
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=30)

    def submit(self, job: CaptureJob) -> bool:
        """入队成功返回 True；被定时任务占用而拒绝返回 False。

        判定和入队在同一把锁里完成——先查询再提交会有竞态窗口，
        期间定时任务可能刚好插进来。
        """
        with self._lock:
            if job.source != "task" and self._task_inflight > 0 and not self._stale_locked():
                print(f"[worker] Rejected {job.symbol} src={job.source} "
                      f"— 定时任务截图进行中 (task_inflight={self._task_inflight})")
                return False
            if job.source == "task":
                self._task_inflight += 1
            self._queue.put(job)
            depth = self._queue.qsize()
        print(f"[worker] Queued {job.symbol} {job.timeframes} "
              f"src={job.source} (depth={depth})")
        return True

    def _stale_locked(self) -> bool:
        """当前 job 是否已卡死。调用方必须持有 _lock。

        卡死的 task job 走不到 finally，task_inflight 永远归不了零，
        手动截图就被永久拒绝——这时优先级机制反而成了故障放大器。
        """
        if self._active_since is None:
            return False
        return (time.time() - self._active_since) > STALE_JOB_SECONDS

    def stats(self) -> dict:
        with self._lock:
            running_for = (round(time.time() - self._active_since, 1)
                           if self._active_since else None)
            return {"depth": self._queue.qsize(),
                    "task_inflight": self._task_inflight,
                    "running_for": running_for,
                    "stale": self._stale_locked()}

    def _capture_in_subprocess(self, job: CaptureJob):
        """在独立进程组里执行截图，超预算 SIGKILL 整棵树（含 Chromium）。

        同步 Playwright 存在不受 action/navigation 超时管辖的调用
        （download.save_as 等），线程内超时防不住全部挂死路径——
        两次生产事故（各挂 2~4 小时、堵死整个队列）之后，进程级
        硬杀是唯一可证明收敛的兜底。
        """
        deadline = (CAPTURE_BASE_OVERHEAD + PER_TF_BUDGET * len(job.timeframes)
                    + SUBPROC_KILL_GRACE)
        fd, out_path = tempfile.mkstemp(suffix=".json", prefix="capture-")
        os.close(fd)
        here = os.path.dirname(os.path.abspath(__file__))
        proc = subprocess.Popen(
            [sys.executable, os.path.join(here, "capture_worker.py"), out_path],
            stdin=subprocess.PIPE, text=True, cwd=here,
            start_new_session=True)          # 独立进程组：killpg 能带走 Chromium
        try:
            proc.communicate(json.dumps({"symbol": job.symbol,
                                         "timeframes": job.timeframes}),
                             timeout=deadline)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.wait()
            job.error = f"截图子进程超时（>{deadline}s），已强杀进程树"
            print(f"[worker] KILLED {job.symbol}: exceeded {deadline}s")
            return
        finally:
            try:
                with open(out_path, encoding="utf-8") as f:
                    result = json.load(f)
            except (OSError, json.JSONDecodeError):
                result = None
            try:
                os.remove(out_path)
            except OSError:
                pass
        if result is None:
            job.error = job.error or f"截图子进程异常退出（exit {proc.returncode}）"
        elif result.get("error"):
            job.error = result["error"]
        else:
            job.result = result.get("paths") or []

    def _run(self):
        while self._running:
            try:
                job = self._queue.get(timeout=1)
            except queue.Empty:
                continue

            if job is None:
                break

            with self._lock:
                self._active_since = time.time()
            started = time.time()
            try:
                self._capture_in_subprocess(job)
            except Exception as e:
                job.error = str(e)
                print(f"[worker] Error: {e}")
            finally:
                # 计数必须在 done_event 之前归零，否则调用方拿到响应后立刻重试仍会被拒
                with self._lock:
                    self._active_since = None
                    if job.source == "task":
                        self._task_inflight = max(0, self._task_inflight - 1)
                print(f"[worker] Done {job.symbol} src={job.source} "
                      f"in {time.time() - started:.1f}s (depth={self._queue.qsize()})")
                job.done_event.set()

        print("[worker] Stopped")
