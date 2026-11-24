from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lstest.scripts.serial_progress import ProgressScanner, verdict


class ProgressScannerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "serial_logs"
        self.dir.mkdir(parents=True)
        self.log = self.dir / "serial_COM6_HISI_C.log"

    def tearDown(self):
        self._tmp.cleanup()

    def append(self, rows):
        with self.log.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(row + "\n")

    def test_incremental_scan_only_counts_new_lines(self):
        self.append([
            "[2026-11-24T12:00:00+08:00] COM6 HISI_C #1 [V][devctl # LS-OTA] ota write, offset 1024, len 1040",
            "[2026-11-24T12:00:01+08:00] COM6 HISI_C #2 [I][ota # LS-OTA] APP upgrade end, want reboot...",
        ])
        scanner = ProgressScanner(self.dir)
        first = scanner.scan()
        self.assertEqual(first["new_lines_total"], 2)
        self.assertEqual(first["stages"]["download_progress"], 1)
        self.assertEqual(first["stages"]["upgrade_end"], 1)
        self.assertEqual(first["ports"]["serial_COM6_HISI_C.log"]["max_ota_offset"], 1024)

        second = scanner.scan()
        self.assertEqual(second["new_lines_total"], 0)

        self.append([
            "[2026-11-24T12:05:00+08:00] COM6 HISI_C #3 [I][devctl # LS-OTA] ota download complete, will reboot...",
            "[2026-11-24T12:05:01+08:00] COM6 HISI_C #4 [2026-11-24 12:05:01][I] venus ap appliction(V4.99.11@Nov 19 2026 09:53:08)",
        ])
        third = scanner.scan()
        self.assertEqual(third["new_lines_total"], 2)
        self.assertEqual(third["stages"]["download_complete"], 1)
        self.assertIn("V4.99.11@Nov 19 2026 09:53:08", third["versions"])

    def test_verdict_covers_the_four_states(self):
        self.assertEqual(verdict({"stages": {"ota_fail": 1}, "new_lines_total": 3}), "本轮出现 OTA 失败标记，需立即取证")
        self.assertEqual(
            verdict({"stages": {"download_complete": 1}, "versions": {"a": 1, "b": 1}, "new_lines_total": 3}),
            "本轮完成下载且出现两个以上版本串，需判断是升级生效还是回滚",
        )
        self.assertEqual(verdict({"stages": {"download_progress": 4}, "new_lines_total": 4}), "本轮仍在下载")
        self.assertEqual(verdict({"stages": {}, "new_lines_total": 0}), "本轮无新增日志")


if __name__ == "__main__":
    unittest.main()
