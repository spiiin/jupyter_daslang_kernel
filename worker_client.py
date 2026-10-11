"""Killable runtime with checkpoints owned by the Jupyter process."""
from collections import deque
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


class WorkerInterrupted(Exception):
    pass


class WorkerCrashed(Exception):
    pass


@dataclass
class Worker:
    process: subprocess.Popen
    replies: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=256))
    errors: deque = field(default_factory=lambda: deque(maxlen=20))
    restored: bool = False


@dataclass
class Request:
    worker: Worker
    id: int
    cancelled: bool = False


class WorkerClient:
    def __init__(self, script=None):
        self.module_worker = script is None and bool(__package__)
        self.script = Path(script or Path(__file__).with_name("worker.py")).resolve()
        self.state = {"variables": {}, "definitions": {}, "values": {}}
        self.worker = None
        self.active = None
        self.lock = threading.RLock()
        self.next_id = 0
        self.started = threading.Event()

    def _start(self):
        command = [sys.executable, "-u", "-m", __package__ + ".worker"] if self.module_worker else [sys.executable, "-u", str(self.script)]
        process = subprocess.Popen(command,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1",
                                            "DAS_WORKER_PARENT_PID": str(os.getpid())},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        worker = Worker(process)

        def read_replies():
            try:
                for line in process.stdout:
                    try:
                        worker.replies.put(json.loads(line))
                    except ValueError:
                        worker.errors.append(line.rstrip())
            finally:
                worker.replies.put(None)
                process.stdout.close()

        def read_errors():
            try:
                for line in process.stderr:
                    worker.errors.append(line.rstrip())
            finally:
                process.stderr.close()

        threading.Thread(target=read_replies, daemon=True).start()
        threading.Thread(target=read_errors, daemon=True).start()
        self.worker = worker
        return worker

    def _terminate(self, worker):
        if worker.process.poll() is None:
            worker.process.kill()
        worker.process.wait(timeout=5)
        if worker.process.stdin and not worker.process.stdin.closed:
            worker.process.stdin.close()
        if self.worker is worker:
            self.worker = None

    def _request(self, worker, operation, on_output=None, timeout=None, **content):
        deadline = time.monotonic() + timeout if timeout is not None else None
        output = {"stdout": [], "stderr": []}
        with self.lock:
            self.next_id += 1
            request = Request(worker, self.next_id)
            self.active = request
            self.started.clear()
            payload = json.dumps({"id": request.id, "op": operation, **content}) + "\n"
            try:
                worker.process.stdin.write(payload)
                worker.process.stdin.flush()
            except (OSError, ValueError) as error:
                self.active = None
                self._terminate(worker)
                raise WorkerCrashed("Runtime process stopped before receiving the cell") from error
        try:
            while True:
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError("Runtime shutdown timed out")
                reply = worker.replies.get(timeout=max(0.01, deadline - time.monotonic()) if deadline is not None else None)
                if reply and reply.get("id") == request.id and reply.get("event") == "stream":
                    if on_output:
                        on_output(reply["name"], reply["text"])
                    else:
                        output[reply["name"]].append(reply["text"])
                    continue
                with self.lock:
                    if reply and reply.get("id") == request.id and reply.get("event") == "running":
                        self.started.set()
                        continue
                    if request.cancelled:
                        raise WorkerInterrupted("Execution interrupted. State restored to the last completed cell.")
                    if reply is None:
                        code = worker.process.poll()
                        detail = "\n".join(worker.errors)
                        self._terminate(worker)
                        raise WorkerCrashed(f"Runtime process exited ({code}). Last completed state is preserved.\n{detail}".rstrip())
                    if reply.get("id") != request.id:
                        continue
                    if reply["status"] == "checkpoint_error" or "state" not in reply:
                        self._terminate(worker)
                        raise WorkerCrashed(reply.get("error", {}).get("evalue", "Could not checkpoint the runtime"))
                    if operation == "execute":
                        self.state = reply["state"]
                    self.active = None
                    reply["output"] = {name: "".join(chunks) for name, chunks in output.items()}
                    return reply
        except KeyboardInterrupt:
            self.interrupt()
            raise WorkerInterrupted("Execution interrupted. State restored to the last completed cell.") from None
        except (WorkerInterrupted, WorkerCrashed):
            raise
        except Exception:
            with self.lock:
                self._terminate(worker)
            raise
        finally:
            with self.lock:
                if self.active is request:
                    self.active = None

    def execute(self, code, on_output=None, cell_label=None):
        with self.lock:
            worker = self.worker
            if worker is not None and worker.process.poll() is not None:
                self._terminate(worker)
                worker = None
            if worker is None:
                worker = self._start()
        if not worker.restored and code.strip() == "%reset":
            worker.restored = True
        if not worker.restored:
            reply = self._request(worker, "restore", state=self.state)
            if reply["status"] != "ok":
                with self.lock:
                    self._terminate(worker)
                raise WorkerCrashed("Could not restore session: " + reply["error"]["evalue"])
            worker.restored = True
        return self._request(worker, "execute", code=code, on_output=on_output, cell_label=cell_label)

    def interrupt(self):
        with self.lock:
            if self.active is None:
                return False
            self.active.cancelled = True
            self._terminate(self.active.worker)
            return True

    def close(self):
        with self.lock:
            worker = self.worker
            busy = self.active is not None
            if self.active:
                self.active.cancelled = True
            if worker is None:
                return
            if busy:
                self._terminate(worker)
                return
        try:
            reply = self._request(worker, "shutdown", timeout=2)
            self.state = reply["state"]
            worker.process.wait(timeout=2)
        except (WorkerCrashed, WorkerInterrupted, TimeoutError, queue.Empty, subprocess.TimeoutExpired):
            pass
        finally:
            with self.lock:
                self._terminate(worker)
