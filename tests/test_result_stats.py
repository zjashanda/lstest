from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lstest.scripts.result_stats import ResultSummary, aggregate_summaries, total_line


HEADER = (
    "round,final_status,round_outcome,wake_result_count,asr_result_count,"
    "multi_result,started_at,ended_at\n"
)
ROW1 = "1,PASS,hit,1,1,False,2026-10-24 04:00:00.000,2026-10-24T04:00:40.000+08:00\n"
ROW2 = "2,PASS,misheard,1,1,False,2026-10-24 04:01:00.000,2026-10-24T04:01:40.000+08:00\n"


class ResultStatsTests(unittest.TestCase):
    def test_incremental_read_and_aggregate(self):
        with TemporaryDirectory() as tmp:
            result_dir = Path(tmp)
            results = result_dir / "results.csv"
            results.write_text(HEADER + ROW1, encoding="utf-8")

            state = ResultSummary(result_dir)
            first = state.update()
            self.assertEqual(first["rounds"], 1)
            self.assertEqual(first["pass"], 1)
            self.assertEqual(first["hit"], 1)
            self.assertEqual(first["avg_round_s"], 40.0)

            with results.open("a", encoding="utf-8", newline="") as handle:
                handle.write(ROW2)
            second = state.update()
            self.assertEqual(second["rounds"], 2)
            self.assertEqual(second["pass"], 2)
            self.assertEqual(second["hit"], 1)
            self.assertEqual(second["misheard"], 1)
            self.assertEqual(second["avg_round_s"], 40.0)
            self.assertEqual(state.update(), second)

            total = aggregate_summaries([second, second])
            self.assertEqual(total["rounds"], 4)
            self.assertEqual(total["pass"], 4)
            self.assertIn("[TOTAL] rounds=4/4", total_line(total, 4, 4, "FINAL"))


if __name__ == "__main__":
    unittest.main()
