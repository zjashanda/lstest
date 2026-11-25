from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lstest.scripts.core import TaskArtifacts
from lstest.scripts.result_layout import LAYOUT_V4, ROOT_ALLOWED_V4


class LedgerFormatTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "result"
        self.artifacts = TaskArtifacts(self.root, "NOAH-999", layout=LAYOUT_V4)
        self.artifacts.freeze_cases([{"case_id": "c1", "scenario": "demo"}])

    def tearDown(self):
        try:
            self.artifacts.finalize("PASS", "teardown")
        except Exception:
            pass
        try:
            self._tmp.cleanup()
        except Exception:
            pass

    def read_tool(self) -> str:
        return self.artifacts.tool_log_path.read_text(encoding="utf-8")

    def test_line_format_is_asctime_level_tag(self):
        self.artifacts.emit("SERIAL_OPEN", message="串口 COM6 已打开", port="COM6", role="CSK6_A", baudrate=115200)
        line = self.read_tool().strip().splitlines()[-1]
        self.assertRegex(line, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} - INFO\s - \[SERIAL\] 串口 COM6 已打开")
        self.assertIn("port=COM6", line)
        self.assertIn("role=CSK6_A", line)
        self.assertIn("baudrate=115200", line)

    def test_level_is_padded_and_error_kept(self):
        self.artifacts.emit("SERIAL_LOSS", level="ERROR", message="COM6 丢失", port="COM6")
        line = self.read_tool().strip().splitlines()[-1]
        self.assertIn(" - ERROR - [SERIAL] ", line)

    def test_round_blank_line(self):
        self.artifacts.emit("CASES_FROZEN", message="用例已冻结", round=1)
        before = self.read_tool()
        self.artifacts.blank_line()
        after = self.read_tool()
        self.assertTrue(after.startswith(before))
        self.assertTrue(after.endswith("\n\n"))

    def test_burn_log_path_is_under_burn_logs(self):
        path = self.artifacts.burn_log_path("burn", 1)
        self.assertEqual(path.parent.name, "burn_logs")
        self.assertRegex(path.name, r"^\d{12}_burn_1\.log$")
        self.assertTrue(path.parent.is_dir())

    def test_layout_has_six_items(self):
        self.artifacts.emit("TASK_STARTED", message="开始")
        self.assertEqual(sorted(ROOT_ALLOWED_V4), sorted(
            ["serial_logs", "burn_logs", "tool.log", "cases.csv", "results.csv", "task.json"]))

    def test_no_device_raw_in_tool_log(self):
        self.artifacts.emit("SERIAL_LINE", message="[D][algo_task] play start", task_log=False,
                            port="COM6", role="CSK6_A", line="[D][algo_task] play start")
        self.assertNotIn("play start", self.read_tool())


if __name__ == "__main__":
    unittest.main()
