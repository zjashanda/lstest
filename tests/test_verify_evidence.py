from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lstest.scripts import finish_gate, verify_evidence


def build(root: Path, *, gate_ok: bool = True, task_id: str = "NOAH-999",
          serial_text: str = "[2026-11-26T10:00:01+08:00] COM4 A #1 keyword:hello haier\n",
          extra_file: bool = False) -> tuple[Path, Path]:
    task = root / "NOAH-999"
    run = task / "result" / "202611261000"
    (run / "serial_logs").mkdir(parents=True)
    (run / "burn_logs").mkdir()
    (run / "tool.log").write_text(
        "2026-11-26 10:00:00,000 - INFO  - [TASK] start\n"
        "2026-11-26 10:00:01,000 - INFO  - [MARK] csk(COM4): intent: hello haier\n",
        encoding="utf-8")
    (run / "cases.csv").write_text("case_order,case_id\n1,G1\n", encoding="utf-8")
    (run / "results.csv").write_text("case_id,final_status\nG1,PASS\n", encoding="utf-8")
    (run / "task.json").write_text(json.dumps({"task": {"task_id": task_id, "status": "PASS"}}), encoding="utf-8")
    (run / "serial_logs" / "a.log").write_text(serial_text, encoding="utf-8")
    if extra_file:
        (run / "extra.txt").write_text("x", encoding="utf-8")
    repo = root / "repo"
    (repo / "lstest" / "markers").mkdir(parents=True)
    (repo / "lstest" / "markers" / "catalog.json").write_text(
        json.dumps({"markers": [{"code": "intent"}, {"code": "wakeup"}]}), encoding="utf-8")
    finish_gate.main(["--task-dir", str(task), "--repo-root", str(repo), "--write"])
    if not gate_ok:
        gate = json.loads((run / "gate.json").read_text(encoding="utf-8"))
        gate["ok"] = False
        gate["errors"] = [{"code": "FORCED", "message": "forced"}]
        (run / "gate.json").write_text(json.dumps(gate), encoding="utf-8")
    return task, run


class VerifyEvidenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_clean_evidence_passes(self):
        task, run = build(self.root)
        report = verify_evidence.verify(run, task)
        self.assertTrue(report["ok"], report["problems"])

    def test_missing_gate_json_fails(self):
        task, run = build(self.root)
        (run / "gate.json").unlink()
        report = verify_evidence.verify(run, task)
        self.assertFalse(report["ok"])
        self.assertTrue(any("gate.json" in p for p in report["problems"]))

    def test_gate_not_ok_fails(self):
        task, run = build(self.root, gate_ok=False)
        report = verify_evidence.verify(run, task)
        self.assertFalse(report["ok"])

    def test_tampered_serial_breaks_marker_check(self):
        task, run = build(self.root, serial_text="[2026-11-26T10:00:01+08:00] COM4 A #1 nothing\n")
        report = verify_evidence.verify(run, task)
        self.assertFalse(report["ok"])
        self.assertTrue(any("串口原文" in p for p in report["problems"]))

    def test_unlisted_extra_file_fails(self):
        task, run = build(self.root)
        (run / "after_gate.txt").write_text("late", encoding="utf-8")
        report = verify_evidence.verify(run, task)
        self.assertFalse(report["ok"])
        self.assertTrue(any("未登记" in p for p in report["problems"]))

    def test_task_id_mismatch_fails(self):
        task, run = build(self.root, task_id="hisense001_10rounds")
        report = verify_evidence.verify(run, task)
        self.assertFalse(report["ok"])

    def test_missing_case_result_fails(self):
        task, run = build(self.root)
        (run / "results.csv").write_text("case_id,final_status\n", encoding="utf-8")
        report = verify_evidence.verify(run, task)
        self.assertFalse(report["ok"])

    def test_tool_log_time_outside_serial_span_fails(self):
        task, run = build(self.root,
                          serial_text="[2026-11-26T09:00:01+08:00] COM4 A #1 keyword:hello haier\n")
        with (run / "tool.log").open("a", encoding="utf-8") as fh:
            fh.write("2026-11-26 23:00:00,000 - INFO  - [MARK] csk(COM4): intent: hello haier\n")
        report = verify_evidence.verify(run, task)
        self.assertFalse(report["ok"])


if __name__ == "__main__":
    unittest.main()
