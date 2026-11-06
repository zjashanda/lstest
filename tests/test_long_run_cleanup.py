from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lstest.scripts.timeline import TimelineLedger, ToolLogValidator


class LongRunCleanupTests(unittest.TestCase):
    def test_closed_timeline_cases_are_released(self):
        ledger = TimelineLedger(lambda _: None, lambda: "2026-01-01 00:00:00.000")
        for index in range(10000):
            case_id = f"case_{index:05d}"
            ledger.start_case(
                case_id, epoch=0, position=index + 1, total=10000,
                scenario="powercycle", text="img",
            )
            ledger.close_case(case_id, status="PASS")
        self.assertEqual(len(ledger._cases), 0)
        self.assertEqual(len(ledger._closed_cases), 10000)

    def test_tool_log_validator_streams_large_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tool.log"
            with path.open("w", encoding="utf-8", newline="\n") as handle:
                for _ in range(20000):
                    handle.write("2026-01-01 00:00:00.000 [SYSTEM] ok\n")
            self.assertEqual(ToolLogValidator().validate_path(path), [])


if __name__ == "__main__":
    unittest.main()
