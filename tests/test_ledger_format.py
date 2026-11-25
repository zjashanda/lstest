from __future__ import annotations

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
        (Path(self._tmp.name) / "result").mkdir(parents=True, exist_ok=True)
        audio = Path(self._tmp.name) / "wake_tmall.wav"
        audio.write_bytes(b"RIFFdemo")
        self.artifacts.freeze_cases([
            {"case_id": "c1", "scenario": "demo", "audio_file": str(audio)},
        ])

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

    def last_line(self) -> str:
        return self.read_tool().strip().splitlines()[-1]

    def test_line_format_is_asctime_level_tag(self):
        self.artifacts.emit("SERIAL_OPEN", message="串口 COM6 已打开", port="COM6", role="CSK6_A", baudrate=115200)
        line = self.last_line()
        self.assertRegex(line, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} - INFO\s - \[SERIAL\]")
        self.assertIn("COM6(CSK6_A)", line)
        self.assertIn("串口 COM6 已打开", line)
        self.assertNotIn("channel=", line)
        self.assertNotIn("port=COM6", line)

    def test_level_is_padded_and_error_kept(self):
        self.artifacts.emit("SERIAL_LOSS", level="ERROR", message="COM6 丢失", port="COM6")
        self.assertIn(" - ERROR - [SERIAL] ", self.last_line())

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

    def test_no_duplicate_value_in_tail(self):
        self.artifacts.emit("CASE_CHECK", message="switchIdx: 0", channel="奥普001(COM5)", round=1)
        line = self.last_line()
        self.assertEqual(line.count("switchIdx: 0"), 1)
        self.assertNotIn("channel=", line)

    def test_emit_action_is_one_ledger_line(self):
        self.artifacts.emit_action("播报", case_id="c1", attempt=1)
        line = self.last_line()
        self.assertIn("[ACTION]", line)
        self.assertIn("播报", line)
        self.assertIn("wake_tmall.wav", line)
        self.assertIn("case=c1", line)
        self.assertIn("attempt=1", line)
        self.assertNotIn("channel=", line)
        self.assertNotIn("action=", line)

    def test_emit_action_keeps_audio_file_name(self):
        self.artifacts.emit_action("播报", case_id="c1")
        line = self.last_line()
        self.assertIn("wake_tmall.wav", line)
        self.assertNotIn(str(Path(self._tmp.name)), line)

    def test_emit_paths_records_directories(self):
        self.artifacts.emit_paths(assets=r"D:\zhsh\workspace\assets",
                                  tools=r"D:\zhsh\workspace\tools")
        text = self.read_tool()
        self.assertIn("[ENV] assets=D:\\zhsh\\workspace\\assets", text)
        self.assertIn("[ENV] tools=D:\\zhsh\\workspace\\tools", text)

    def test_emit_action_without_case_has_no_audio(self):
        self.artifacts.emit_action("上电", detail="uut-switch1.on")
        line = self.last_line()
        self.assertIn("上电", line)
        self.assertNotIn("wav", line)

    def test_sha256_is_not_written_to_tool_log(self):
        digest = "64FE5047E7F2805D2BAB2F35B23AED29FB174688ABD43A08B87BD387123802A9"
        self.artifacts.emit("CASE_CHECK", message="固件校验", case_id="c1",
                            status="PASS", reason=f"sha256={digest}")
        line = self.last_line()
        self.assertNotIn("sha256", line)
        self.assertNotIn(digest, line)
        self.assertIn("status=PASS", line)

    def test_timeline_lines_use_ledger_format(self):
        self.artifacts.timeline.system("任务开始: task=NOAH-999")
        line = self.last_line()
        self.assertRegex(line, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} - INFO\s - \[TASK\] 任务开始")


if __name__ == "__main__":
    unittest.main()
