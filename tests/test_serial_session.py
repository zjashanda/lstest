from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from lstest.scripts.serial_session import (
    REASON_INTERRUPT,
    REASON_TASK_DONE,
    IllegalDetach,
    SerialLeaseError,
    SerialSession,
)

PORTS = [
    {"port": "COM10", "role": "CSK6_A", "baud": 115200, "offset": 0},
    {"port": "COM8", "role": "HISI_C", "baud": 1500000, "offset": 0},
]


class SerialSessionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "serial_session.json"

    def tearDown(self):
        self._tmp.cleanup()

    def make(self, task_id="NOAH-411"):
        return SerialSession(self.path, task_id, agent="锦衣卫·bsz001", node="bsz001")

    def test_attach_then_task_done(self):
        session = self.make()
        payload = session.attach(PORTS)
        self.assertEqual(payload["state"], "ATTACHED")
        self.assertEqual(payload["detach_reason"], None)
        self.assertTrue(session.is_attached())
        session.detach(REASON_TASK_DONE, "window complete")
        final = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(final["state"], "DETACHED")
        self.assertEqual(final["detach_reason"], REASON_TASK_DONE)

    def test_interrupt_is_the_only_other_reason(self):
        session = self.make()
        session.attach(PORTS)
        session.detach(REASON_INTERRUPT, "operator stop")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["detach_reason"], "INTERRUPT")

    def test_illegal_detach_is_rejected(self):
        session = self.make()
        session.attach(PORTS)
        with self.assertRaises(IllegalDetach):
            session.detach("IDLE_TOO_LONG")
        with self.assertRaises(IllegalDetach):
            session.detach("")
        self.assertTrue(session.is_attached())

    def test_foreign_live_lease_blocks_second_task(self):
        self.make("NOAH-411").attach(PORTS)
        with self.assertRaises(SerialLeaseError) as ctx:
            self.make("NOAH-412").attach(PORTS)
        self.assertIn("NOAH-411", str(ctx.exception))
        self.assertIn("BLOCKED_SERIAL_LEASE", str(ctx.exception))

    def test_same_owner_resume_records_gap(self):
        session = self.make()
        session.attach(PORTS)
        resumed = session.attach(PORTS)
        self.assertEqual(resumed["resume_count"], 1)
        self.assertEqual(len(resumed["gaps"]), 1)
        self.assertEqual(resumed["gaps"][0]["reason"], "cross_run_resume")

    def test_stale_lease_can_be_taken_over_and_records_gap(self):
        first = self.make("NOAH-411")
        first.attach(PORTS)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["heartbeat_at"] = (datetime.now().astimezone() - timedelta(hours=2)).isoformat(timespec="seconds")
        self.path.write_text(json.dumps(payload), encoding="utf-8")

        without_flag = SerialSession(self.path, "NOAH-412")
        with self.assertRaises(SerialLeaseError):
            without_flag.attach(PORTS)

        takeover = SerialSession(self.path, "NOAH-412").attach(PORTS, takeover_stale=True)
        self.assertEqual(takeover["task_id"], "NOAH-412")
        self.assertEqual(len(takeover["gaps"]), 1)
        self.assertIn("stale_lease_takeover", takeover["gaps"][0]["reason"])

    def test_heartbeat_updates_offsets(self):
        session = self.make()
        session.attach(PORTS)
        session.heartbeat({"COM10": 1024})
        state = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(state["ports"]["COM10"]["offset"], 1024)

    def test_heartbeat_requires_attached(self):
        session = self.make()
        with self.assertRaises(Exception):
            session.heartbeat({"COM10": 1})


if __name__ == "__main__":
    unittest.main()
