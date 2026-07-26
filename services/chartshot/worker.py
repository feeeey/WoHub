import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from config import STALE_JOB_SECONDS


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

    def _run(self):
        from screenshot import capture_chart

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
                paths = capture_chart(job.symbol, job.timeframes)
                job.result = paths
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
