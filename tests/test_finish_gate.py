from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lstest.scripts import finish_gate


def build_task(root: Path, *, task_id: str = "NOAH-999", marker: str = "intent",
               bare: bool = False, extra_dir: str = "", status: str = "PASS") -> Path:
    task = root / "NOAH-999"
    run = task / "result" / "202611251200"
    (run / "serial_logs").mkdir(parents=True)
    (run / "burn_logs").mkdir()
    lines = [
        "2026-11-25 12:00:00,000 - INFO  - [TASK] 任务开始: task=NOAH-999",
        f"2026-11-25 12:00:01,000 - INFO  - [MARK] csk(COM4): {marker}: hello",
    ]
    if bare:
        lines.append("  csk(COM4): intent: hello")
    (run / "tool.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (run / "cases.csv").write_text("case_order,case_id\n1,G1\n", encoding="utf-8")
    (run / "results.csv").write_text("case_id,final_status\nG1,PASS\n", encoding="utf-8")
    (run / "task.json").write_text(json.dumps({"task": {"task_id": task_id, "status": status}}), encoding="utf-8")
    (run / "serial_logs" / "a.log").write_text("[12:00] line\n", encoding="utf-8")
    if extra_dir:
        (task / "result" / extra_dir).mkdir()
    return task


class FinishGateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = self.root / "repo"
        (self.repo / "lstest" / "markers").mkdir(parents=True)
        (self.repo / "lstest" / "markers" / "catalog.json").write_text(
            json.dumps({"markers": [{"code": "intent", "category": "wake", "desc": ""}]}),
            encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def gate(self, task: Path) -> dict:
        return finish_gate.run_gate(task, None, self.repo)

    def test_clean_task_passes(self):
        task = build_task(self.root)
        report = self.gate(task)
        self.assertTrue(report["ok"], report["errors"])

    def test_bare_marker_fails(self):
        task = build_task(self.root, bare=True)
        codes = {e["code"] for e in self.gate(task)["errors"]}
        self.assertIn("BARE_MARKER", codes)

    def test_task_id_mismatch_fails(self):
        task = build_task(self.root, task_id="hisense001_10rounds")
        codes = {e["code"] for e in self.gate(task)["errors"]}
        self.assertIn("TASK_ID_MISMATCH", codes)

    def test_extra_result_dir_fails(self):
        task = build_task(self.root, extra_dir="lstest-framework")
        codes = {e["code"] for e in self.gate(task)["errors"]}
        self.assertIn("RESULT_ROOT_EXTRA", codes)

    def test_unknown_marker_code_fails(self):
        task = build_task(self.root, marker="keyword")
        codes = {e["code"] for e in self.gate(task)["errors"]}
        self.assertIn("UNKNOWN_MARKER_CODE", codes)

    def test_missing_item_fails(self):
        task = build_task(self.root)
        (task / "result" / "202611251200" / "results.csv").unlink()
        codes = {e["code"] for e in self.gate(task)["errors"]}
        self.assertIn("MISSING_ITEMS", codes)

    def test_empty_serial_fails(self):
        task = build_task(self.root)
        (task / "result" / "202611251200" / "serial_logs" / "a.log").write_text("", encoding="utf-8")
        codes = {e["code"] for e in self.gate(task)["errors"]}
        self.assertIn("SERIAL_ALL_EMPTY", codes)

    def test_longrun_running_warns(self):
        task = build_task(self.root)
        work = task / "work"
        work.mkdir()
        (work / "longrun_soak.state.json").write_text(
            json.dumps({"status": "running", "completed": 20, "target_rounds": 500}), encoding="utf-8")
        codes = {w["code"] for w in self.gate(task)["warnings"]}
        self.assertIn("LONGRUN_RUNNING", codes)

    def test_longrun_short_warns(self):
        task = build_task(self.root)
        work = task / "work"
        work.mkdir()
        (work / "longrun_soak.state.json").write_text(
            json.dumps({"status": "completed", "completed": 20, "target_rounds": 500}), encoding="utf-8")
        codes = {w["code"] for w in self.gate(task)["warnings"]}
        self.assertIn("LONGRUN_SHORT", codes)


class RequirePassTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = self.root / "repo"
        (self.repo / "lstest" / "markers").mkdir(parents=True)
        (self.repo / "lstest" / "markers" / "catalog.json").write_text(
            json.dumps({"markers": [{"code": "intent", "category": "wake", "desc": ""}]}),
            encoding="utf-8")
        self.task = build_task(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_gate_json_blocks(self):
        run = next((self.task / "result").glob("2026*"))
        problems = finish_gate.require_pass(run)
        self.assertTrue(any("缺少 gate.json" in p for p in problems), problems)

    def test_passing_gate_allows_upload(self):
        finish_gate.main(["--task-dir", str(self.task), "--repo-root", str(self.repo), "--write"])
        run = next((self.task / "result").glob("2026*"))
        self.assertEqual(finish_gate.require_pass(run), [])

    def test_modified_tool_log_blocks(self):
        finish_gate.main(["--task-dir", str(self.task), "--repo-root", str(self.repo), "--write"])
        run = next((self.task / "result").glob("2026*"))
        with (run / "tool.log").open("a", encoding="utf-8") as fh:
            fh.write("2026-11-25 12:00:09,000 - INFO  - [TASK] tampered\n")
        problems = finish_gate.require_pass(run)
        self.assertTrue(any("被修改" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
