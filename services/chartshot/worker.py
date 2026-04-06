import queue
import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CaptureJob:
    symbol: str
    timeframes: list
    job_id: str = ""
    result: Optional[list] = field(default=None, repr=False)
    error: Optional[str] = field(default=None, repr=False)
    done_event: threading.Event = field(default_factory=threading.Event, repr=False)


class CaptureWorker:
    def __init__(self):
        self._queue = queue.Queue()
        self._thread = None
        self._running = False

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

    def submit(self, job: CaptureJob):
        self._queue.put(job)
        print(f"[worker] Queued {job.symbol} {job.timeframes} (depth={self._queue.qsize()})")

    def _run(self):
        from screenshot import capture_chart

        while self._running:
            try:
                job = self._queue.get(timeout=1)
            except queue.Empty:
                continue

            if job is None:
                break

            try:
                paths = capture_chart(job.symbol, job.timeframes)
                job.result = paths
            except Exception as e:
                job.error = str(e)
                print(f"[worker] Error: {e}")
            finally:
                job.done_event.set()

        print("[worker] Stopped")
