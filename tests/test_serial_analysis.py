from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lstest.scripts.serial_analysis import analyze_port, analyze_run


class SerialAnalysisTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.run_dir = self.root / "run"
        (self.run_dir / "serial_logs").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def write_log(self, name, rows):
        path = self.run_dir / "serial_logs" / name
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        return path

    def test_port_facts_and_gap_detection(self):
        path = self.write_log("serial_COM10_CSK6_A.log", [
            "[2026-11-24T09:00:00+08:00] COM10 CSK6_A #1 wifi connected !!!",
            "[2026-11-24T09:00:10+08:00] COM10 CSK6_A #2 ota download start",
            "[2026-11-24T09:05:00+08:00] COM10 CSK6_A #3 ota success",
        ])
        report = analyze_port(path)
        self.assertEqual(report["lines"], 3)
        self.assertEqual(report["ota_success_markers"], 1)
        self.assertEqual(report["network_markers"], 1)
        self.assertEqual(report["gaps_over_threshold"], 1)
        self.assertGreaterEqual(report["max_gap_s"], 120)

    def test_ota_not_observed_when_no_keywords(self):
        self.write_log("serial_COM8_HISI_C.log", [
            "[2026-11-24T09:00:00+08:00] COM8 HISI_C #1 hello",
            "[2026-11-24T09:00:01+08:00] COM8 HISI_C #2 world",
        ])
        report = analyze_run(self.run_dir, {"state": "DETACHED", "detach_reason": "TASK_DONE"})
        self.assertEqual(report["ota_status"], "NOT_OBSERVED")
        self.assertEqual(report["totals"]["ports_with_data"], 1)

    def test_failure_markers_win(self):
        self.write_log("serial_COM6_HISI_C.log", [
            "[2026-11-24T09:00:00+08:00] COM6 HISI_C #1 ota verify fail",
            "[2026-11-24T09:00:02+08:00] COM6 HISI_C #2 ota success",
        ])
        report = analyze_run(self.run_dir)
        self.assertEqual(report["ota_status"], "OBSERVED_FAILURE")
        self.assertEqual(report["totals"]["ota_failure_markers"], 1)


if __name__ == "__main__":
    unittest.main()
