import sys
from pathlib import Path
import tempfile
from time import monotonic, sleep
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from edit_worker import EditWorker


class PreviewWorkerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.worker = EditWorker(sys.executable, self.root)
        self.addCleanup(self.worker._stop)
        self.worker.BUSY_GRACE_SECONDS = .03

    def set_child(self, code):
        self.worker._command = lambda _: [sys._base_executable, "-u", "-c", code]

    def test_long_running_preview_is_killed_before_returning_timeout(self):
        self.set_child("import sys,time; sys.stdin.readline(); time.sleep(60)")
        result = self.worker.preview(self.root, self.root, "sample", self.root, .18)
        self.assertIn("等待时限", result)
        self.assertIsNone(self.worker.process)
        self.assertFalse((self.root / "WORKER_TIMING.json").exists())

    def test_failed_start_can_fall_back_to_an_isolated_preview(self):
        self.set_child("raise SystemExit(2)")
        self.assertIsNone(self.worker.preview(self.root, self.root, "sample", self.root, 2))
        self.assertIsNone(self.worker.process)

    def test_busy_worker_uses_existing_isolated_route(self):
        with self.worker.lock:
            self.assertIsNone(self.worker.preview(self.root, self.root, "sample", self.root, 2))
        self.assertIsNone(self.worker.process)

    def test_idle_worker_is_retired(self):
        self.set_child("import sys; sys.stdin.read()")
        self.worker.IDLE_SECONDS = .12
        self.worker.warm(self.root, "甲", "乙")
        process = self.worker.process
        self.assertIsNotNone(process)
        until = monotonic() + 3
        while self.worker.process is not None and monotonic() < until:
            sleep(.04)
        self.assertIsNone(self.worker.process)
        process.wait(timeout=1)


if __name__ == "__main__":
    unittest.main()
