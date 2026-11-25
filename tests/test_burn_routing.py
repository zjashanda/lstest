from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lstest.scripts.core import TaskArtifacts
from lstest.scripts.result_layout import LAYOUT_V4


class BurnRoutingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "result"
        self.artifacts = TaskArtifacts(self.root, "NOAH-999", layout=LAYOUT_V4)

    def tearDown(self):
        try:
            self.artifacts.finalize("PASS", "teardown")
        except Exception:
            pass
        try:
            self._tmp.cleanup()
        except Exception:
            pass

    def test_burn_role_goes_to_burn_logs(self):
        path = self.artifacts.serial_log_path("COM15", "burn_tool")
        self.assertEqual(path.parent.name, "burn_logs")
        self.assertRegex(path.name, r"^\d{12}_burn_1\.log$")

    def test_burn_path_is_stable_within_run(self):
        first = self.artifacts.serial_log_path("COM15", "burn")
        second = self.artifacts.serial_log_path("COM15", "burn")
        self.assertEqual(first, second)

    def test_device_roles_still_go_to_serial_logs(self):
        path = self.artifacts.serial_log_path("COM6", "CSK6_A", "海尔001")
        self.assertEqual(path.parent.name, "serial_logs")
        self.assertIn("海尔001", path.name)

    def test_next_burn_seq_increments_by_existing_files(self):
        first = self.artifacts.next_burn_seq()
        self.assertEqual(first, 1)
        self.artifacts.serial_log_path("COM15", "burn")
        self.assertEqual(self.artifacts.next_burn_seq(), 2)


if __name__ == "__main__":
    unittest.main()
