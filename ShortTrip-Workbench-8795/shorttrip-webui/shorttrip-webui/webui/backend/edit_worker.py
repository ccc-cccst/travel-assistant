"""Supervise one reusable preview process without sharing the web server's state."""

import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Lock, Timer
from time import monotonic, sleep
from uuid import uuid4


class EditWorker:
    IDLE_SECONDS = 120
    BUSY_GRACE_SECONDS = .5

    def __init__(self, python, root):
        self.python = python
        self.root = root
        self.lock = Lock()
        self.process = None
        self.data = None
        self.timer = None

    def _command(self, data):
        if os.name != "nt":
            return [self.python, "-B", "-u", "-X", "utf8", "-m",
                    "chinatravel.personalization.edit_worker", "--data", str(data)]
        if Path(self.python).resolve() != Path(sys.executable).resolve():
            return None
        # Run the real executable so terminating the worker also terminates its Python code.
        site = Path(self.python).parent.parent / "Lib" / "site-packages"
        code = ("import runpy, site, sys; site.addsitedir(sys.argv.pop(1)); "
                "runpy.run_module('chinatravel.personalization.edit_worker', run_name='__main__')")
        return [sys._base_executable, "-B", "-u", "-X", "utf8", "-c", code, str(site), "--data", str(data)]

    def _cancel_retirement(self):
        if self.timer:
            self.timer.cancel()
            self.timer = None

    def _stop(self):
        self._cancel_retirement()
        process, self.process, self.data = self.process, None, None
        if process is None:
            return
        if process.poll() is None:
            process.kill()
        process.wait()
        try:
            process.stdin.close()
        except OSError:
            pass

    def _retire_when_idle(self):
        self._cancel_retirement()

        def retire():
            with self.lock:
                if self.timer is timer:
                    self._stop()

        timer = Timer(self.IDLE_SECONDS, retire)
        timer.daemon = True
        self.timer = timer
        timer.start()

    def _ensure(self, data):
        if self.process is not None and self.process.poll() is None and self.data == data:
            return True
        self._stop()
        command = self._command(data)
        if command is None:
            return False
        logs = self.root / "result" / "workbench_runtime" / "edit-workers"
        logs.mkdir(parents=True, exist_ok=True)
        with (logs / (uuid4().hex + ".log")).open("w", encoding="utf-8") as log:
            self.process = subprocess.Popen(command, cwd=self.root, stdin=subprocess.PIPE,
                stdout=log, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.data = data
        return True

    def _submit(self, payload):
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def warm(self, data, origin, destination):
        if not origin or not destination or not self.lock.acquire(blocking=False):
            return
        try:
            if self.process is not None and self.process.poll() is None and self.data == data:
                self._retire_when_idle()
                return
            if self._ensure(data):
                self._submit({"warm":{"start_city":origin, "target_city":destination}})
                self._retire_when_idle()
        except OSError:
            self._stop()
        finally:
            self.lock.release()

    def preview(self, data, source, candidate, folder, timeout):
        """Return None to use the original subprocess path, otherwise a failure message or empty string."""
        if not self.lock.acquire(timeout=self.BUSY_GRACE_SECONDS):
            return None
        try:
            self._cancel_retirement()
            if not self._ensure(data):
                return None
            self._submit({"source":str(source), "candidate":candidate, "folder":str(folder)})
            end = monotonic() + timeout
            marker = folder / "WORKER_TIMING.json"
            while monotonic() < end:
                if marker.is_file():
                    self._retire_when_idle()
                    return ""
                if self.process.poll() is not None:
                    break
                sleep(.045)
            if self.process.poll() is None:
                self._stop()
                return "修改复核超过等待时限，请回到原行程后重试。"
            self._stop()
            return "修改进程退出，请回到原行程后重试。" if (folder / "run").exists() else None
        except OSError:
            self._stop()
            return "本机无法启动修改复核进程。" if (folder / "run").exists() else None
        finally:
            self.lock.release()
