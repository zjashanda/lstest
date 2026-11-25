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


class CapabilityBaselineTests(unittest.TestCase):
    def test_absent_capability_skips_playback_requirement(self):
        from lstest.scripts.interaction_acceptance import evaluate_round

        ok, reason = evaluate_round("hit", "wake_playback", wake_playback_count=0,
                                    capabilities={"wake_playback": False})
        self.assertTrue(ok, reason)

    def test_default_capability_still_requires_playback(self):
        from lstest.scripts.interaction_acceptance import evaluate_round

        ok, reason = evaluate_round("hit", "wake_playback", wake_playback_count=0)
        self.assertFalse(ok)
        self.assertIn("未观察到唤醒播报", reason)

    def test_baseline_flags_only_the_minority(self):
        from lstest.scripts.interaction_acceptance import baseline_missing

        rows = [
            {"case_id": "G7", "features": {"wake_result": True, "protocol_frame": True}},
            {"case_id": "G8", "features": {"wake_result": True, "protocol_frame": True}},
            {"case_id": "G9", "features": {"wake_result": True, "protocol_frame": False}},
        ]
        findings = baseline_missing(rows)
        self.assertEqual(len(findings), 1)
        self.assertIn("G9", findings[0])
        self.assertIn("protocol_frame", findings[0])

    def test_baseline_silent_when_everyone_lacks_it(self):
        from lstest.scripts.interaction_acceptance import baseline_missing

        rows = [
            {"case_id": "G1", "features": {"protocol_frame": False}},
            {"case_id": "G2", "features": {"protocol_frame": False}},
        ]
        self.assertEqual(baseline_missing(rows), [])

    def test_marker_capability_skips_absent_category(self):
        import json
        from lstest.scripts import markers

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps({
                "schema": "lstest.marker-catalog.v1",
                "categories": [{"id": "protocol", "label": "protocol"}],
                "markers": [{"code": "protoRx", "category": "protocol", "desc": "", "display": []}],
            }), encoding="utf-8")
            profile = root / "profile.json"
            profile.write_text(json.dumps({
                "schema": "lstest.project-markers.v1",
                "capabilities": {"markers_absent": ["protocol"]},
                "event_rules": [],
            }), encoding="utf-8")
            extractor = markers.MarkerExtractor.from_paths(catalog, profile)
            self.assertEqual(extractor.report_missing("ap"), [])


if __name__ == "__main__":
    unittest.main()
