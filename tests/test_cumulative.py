from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lstest.scripts import cumulative, finish_gate


class CumulativeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_ledger(self):
        cum = cumulative.load(self.work, "NOAH-413")
        self.assertEqual(cum["schema"], cumulative.SCHEMA)
        self.assertEqual(cum["windows"], 0)

    def test_merge_two_windows(self):
        cum = cumulative.load(self.work, "NOAH-413")
        cum = cumulative.merge_window(cum, {"run": "w1", "started_at": "2026-11-24 15:00:00",
                                            "ended_at": "2026-11-25 15:00:00", "seconds": 86400,
                                            "serial_bytes": 100, "marks": 10,
                                            "counters": {"otaCheck": 5}, "anomalies": 0})
        cum = cumulative.merge_window(cum, {"run": "w2", "started_at": "2026-11-25 16:00:00",
                                            "ended_at": "2026-11-26 02:00:00", "seconds": 36000,
                                            "serial_bytes": 50, "marks": 7,
                                            "counters": {"otaCheck": 3, "wifiState": 2}, "anomalies": 1})
        self.assertEqual(cum["windows"], 2)
        self.assertEqual(cum["total_seconds"], 122400)
        self.assertEqual(cum["total_serial_bytes"], 150)
        self.assertEqual(cum["counters"]["otaCheck"], 8)
        self.assertEqual(cum["counters"]["wifiState"], 2)
        self.assertEqual(cum["anomalies"], 1)

    def test_merge_same_run_replaces_not_adds(self):
        cum = cumulative.load(self.work, "t")
        base = {"run": "w1", "started_at": "2026-11-24 15:00:00", "ended_at": "2026-11-24 16:00:00",
                "seconds": 3600, "serial_bytes": 10, "marks": 1, "counters": {"a": 1}, "anomalies": 0}
        cum = cumulative.merge_window(cum, base)
        cum = cumulative.merge_window(cum, dict(base, seconds=7200, counters={"a": 2}))
        self.assertEqual(cum["windows"], 1)
        self.assertEqual(cum["total_seconds"], 7200)
        self.assertEqual(cum["counters"]["a"], 2)

    def test_render_line_shows_history_and_delta(self):
        cum = cumulative.load(self.work, "t")
        cum = cumulative.merge_window(cum, {"run": "w1", "started_at": "2026-11-24 15:00:00",
                                            "ended_at": "2026-11-25 15:00:00", "seconds": 86400,
                                            "serial_bytes": 1024 ** 3, "marks": 10,
                                            "counters": {"otaCheck": 5}, "anomalies": 0})
        line = cumulative.render_line(cum, {"seconds": 1800, "marks": 3})
        self.assertIn("累计 窗口=1", line)
        self.assertIn("累计", line)
        self.assertIn("本轮增量", line)
        self.assertIn("otaCheckx5", line)

    def test_save_and_load_roundtrip(self):
        cum = cumulative.load(self.work, "t")
        cum = cumulative.merge_window(cum, {"run": "w1", "started_at": "2026-11-24 15:00:00",
                                            "ended_at": "2026-11-24 16:00:00", "seconds": 3600,
                                            "serial_bytes": 1, "marks": 1, "counters": {}, "anomalies": 0})
        cumulative.save(self.work, cum)
        again = cumulative.load(self.work, "t")
        self.assertEqual(again["windows"], 1)
        self.assertEqual(again["total_seconds"], 3600)

    def test_covered_since(self):
        cum = cumulative.load(self.work, "t")
        cum = cumulative.merge_window(cum, {"run": "w1", "started_at": "2026-11-24 15:00:00",
                                            "ended_at": "2026-11-26 12:00:00", "seconds": 1,
                                            "serial_bytes": 1, "marks": 1, "counters": {}, "anomalies": 0})
        self.assertTrue(cumulative.covered_since(cum, "2026-11-26 11:00:00"))
        self.assertFalse(cumulative.covered_since(cum, "2026-11-26 13:00:00"))


class ContinuousGateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _task(self, with_ledger: bool, with_line: bool) -> Path:
        task = self.root / "NOAH-413"
        run = task / "result" / "202611261200"
        (run / "serial_logs").mkdir(parents=True)
        (run / "burn_logs").mkdir()
        body = "2026-11-26 12:00:00,000 - INFO  - [TASK] 窗口开始\n"
        if with_line:
            body += "2026-11-26 12:10:00,000 - INFO  - [TASK] 累计 窗口=3；总时长=44.6h\n"
        (run / "tool.log").write_text(body, encoding="utf-8")
        (run / "cases.csv").write_text("case_order,case_id\n1,monitor_window\n", encoding="utf-8")
        (run / "results.csv").write_text("case_id,final_status\nmonitor_window,PASS\n", encoding="utf-8")
        (run / "task.json").write_text(json.dumps({"task": {"task_id": "NOAH-413", "status": "PASS"},
                                                   "inputs": {"scenario": "ota_continuous_monitor"}}),
                                       encoding="utf-8")
        (run / "serial_logs" / "a.log").write_text("[2026-11-26T12:00:01] line\n", encoding="utf-8")
        repo = self.root / "repo"
        (repo / "lstest" / "markers").mkdir(parents=True, exist_ok=True)
        (repo / "lstest" / "markers" / "catalog.json").write_text(json.dumps({"markers": []}), encoding="utf-8")
        if with_ledger:
            work = task / "work"
            work.mkdir()
            cum = cumulative.load(work, "NOAH-413")
            cum = cumulative.merge_window(cum, {"run": "202611241547", "started_at": "2026-11-24 15:46:05",
                                                "ended_at": "2026-11-25 14:49:40", "seconds": 83015,
                                                "serial_bytes": 1, "marks": 0, "counters": {}, "anomalies": 0})
            cumulative.save(work, cum)
        return task

    def gate(self, task: Path) -> dict:
        return finish_gate.run_gate(task, None, self.root / "repo")

    def test_continuous_task_without_ledger_fails(self):
        codes = {e["code"] for e in self.gate(self._task(False, True))["errors"]}
        self.assertIn("NO_CUMULATIVE_LEDGER", codes)

    def test_continuous_task_without_cumulative_line_fails(self):
        codes = {e["code"] for e in self.gate(self._task(True, False))["errors"]}
        self.assertIn("NO_CUMULATIVE_LINE", codes)

    def test_continuous_task_compliant_passes(self):
        report = self.gate(self._task(True, True))
        self.assertTrue(report["ok"], report["errors"])


if __name__ == "__main__":
    unittest.main()
