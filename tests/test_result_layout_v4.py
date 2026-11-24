from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lstest.scripts.core import CaseResult, TaskArtifacts
from lstest.scripts.result_layout import LAYOUT_V4, ROOT_ALLOWED_V4, TASK_SCHEMA, write_task_manifest


class ResultLayoutTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.result_root = Path(self._tmp.name) / "result"

    def tearDown(self):
        try:
            self.artifacts.finalize("PASS", "teardown")
        except Exception:
            pass
        try:
            self._tmp.cleanup()
        except Exception:
            pass

    def build(self):
        self.artifacts = TaskArtifacts(self.result_root, "NOAH-999", layout="v4")
        self.artifacts.freeze_cases([{"case_id": "c1", "scenario": "s"}])
        return self.artifacts

    def test_flat_mode_writes_into_result_root(self):
        artifacts = self.build()
        self.assertEqual(artifacts.run_dir, self.result_root)
        self.assertEqual(artifacts.task_id, "NOAH-999")
        self.assertTrue((self.result_root / "serial_logs").is_dir())

    def test_running_manifest_has_no_artifacts_yet(self):
        artifacts = self.build()
        path = artifacts.write_task_manifest(status="running", started_at="2026-11-24T12:00:00.000+08:00")
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], TASK_SCHEMA)
        self.assertEqual(payload["task"]["status"], "running")
        self.assertEqual(payload["artifacts"], [])
        self.assertRegex(payload["generated_at"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_final_manifest_covers_the_fixed_layout(self):
        artifacts = self.build()
        (self.result_root / "serial_logs" / "serial_COM10_CSK6_A.log").write_text("line\n", encoding="utf-8")
        artifacts.record_case(CaseResult(case_id="c1", raw_status="PASS", reviewed_status="PASS"))
        path = artifacts.write_task_manifest(
            status="PASS", reason="done",
            inputs={"ports": [{"port": "COM10"}]},
            lifecycle={"session": {"state": "DETACHED", "detach_reason": "TASK_DONE"}},
            summary={"totals": {"lines": 1}},
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        names = {item["path"] for item in payload["artifacts"]}
        self.assertIn("serial_logs/serial_COM10_CSK6_A.log", names)
        self.assertIn("tool.log", names)
        self.assertIn("cases.csv", names)
        self.assertIn("results.csv", names)
        self.assertNotIn("task.json", names)
        for item in payload["artifacts"]:
            self.assertTrue(item["hash"].startswith("sha256:"), item)
            self.assertGreater(item["bytes"], 0, item)

        present = sorted(item.name for item in self.result_root.iterdir())
        self.assertEqual(present, sorted(ROOT_ALLOWED_V4))

    def test_serial_log_name_carries_stamp_device_port_bus_letter(self):
        artifacts = self.build()
        artifact_name = artifacts.serial_log_path("COM10", "CSK6_A", "海尔001").name
        self.assertRegex(artifact_name, r"^\d{12}_海尔001_COM10_csk_a\.log$")
        self.assertTrue(artifacts.serial_log_path("COM8", "HISI_C", "海尔002").name.endswith("_海尔002_COM8_hisi_c.log"))
        self.assertTrue(artifacts.serial_log_path("COM7", None, None).name.endswith("_COM7_other_na.log"))


if __name__ == "__main__":
    unittest.main()
