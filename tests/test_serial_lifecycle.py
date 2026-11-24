from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from lstest.scripts.core import PortSpec, TaskArtifacts
from lstest.scripts.serial_capture import SerialManager
from lstest.scripts.serial_session import IllegalDetach, SerialSession


class FakePort:
    def __init__(self, lines=()):
        self.lines = list(lines)
        self.closed = False

    def readline(self):
        if self.lines:
            return self.lines.pop(0)
        time.sleep(0.01)
        return b""

    def close(self):
        self.closed = True

    def write(self, payload):
        return len(payload)

    def flush(self):
        return None


class SerialLifecycleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.artifacts = TaskArtifacts(self.root / "result", "t", run_name="run")
        self.artifacts.freeze_cases([{"case_id": "c1", "scenario": "test"}])
        self.session = SerialSession(self.root / "logs" / "serial_session.json", "NOAH-411")

    def tearDown(self):
        try:
            self.artifacts.finalize("PASS", "test teardown")
        except Exception:
            pass
        try:
            self._tmp.cleanup()
        except Exception:
            pass

    def make(self, lines=()):
        spec = PortSpec(port="COM_TEST", baudrate=115200, role="CSK6_A")
        manager = SerialManager(
            (spec,), self.artifacts,
            factory=lambda *args, **kwargs: FakePort(lines),
            session=self.session,
        )
        return manager

    def test_idle_feed_never_detaches(self):
        manager = self.make()
        manager.start()
        time.sleep(0.3)
        self.assertIsNone(manager.detach_reason)
        self.assertTrue(self.session.is_attached())
        self.assertGreater(manager.idle_polls.get("COM_TEST", 0), 0)
        manager.finish("TASK_DONE", "idle test")

    def test_stop_without_reason_is_illegal(self):
        manager = self.make()
        manager.start()
        with self.assertRaises(IllegalDetach):
            manager.stop()
        self.assertTrue(self.session.is_attached())
        manager.finish("TASK_DONE", "cleanup")

    def test_illegal_finish_reason_is_rejected(self):
        manager = self.make()
        manager.start()
        with self.assertRaises(IllegalDetach):
            manager.finish("IDLE")
        self.assertTrue(self.session.is_attached())
        manager.finish("INTERRUPT", "cleanup")

    def test_finish_detaches_session_and_closes_port(self):
        manager = self.make([b"hello\r\n"])
        manager.start()
        time.sleep(0.2)
        manager.finish("TASK_DONE", "window complete")
        state = self.session.as_dict()
        self.assertEqual(state["state"], "DETACHED")
        self.assertEqual(state["detach_reason"], "TASK_DONE")
        manager.assert_detached_legally()

    def test_assert_detached_legally_fails_when_still_attached(self):
        manager = self.make()
        manager.start()
        with self.assertRaises(IllegalDetach):
            manager.assert_detached_legally()
        manager.finish("TASK_DONE", "cleanup")

    def test_foreign_lease_blocks_start(self):
        SerialSession(self.root / "logs" / "serial_session.json", "NOAH-410").attach([
            {"port": "COM_TEST", "role": "CSK6_A", "baud": 115200, "offset": 0},
        ])
        manager = self.make()
        with self.assertRaises(Exception):
            manager.start()


if __name__ == "__main__":
    unittest.main()
