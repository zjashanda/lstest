from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lstest.scripts import marker_discovery


CATALOG = {
    "schema": "lstest.marker-catalog.v1",
    "categories": [{"id": "media", "label": "媒体"}, {"id": "protocol", "label": "协议"}],
    "markers": [
        {"code": "volumeSet", "category": "media", "desc": "音量设置", "display": []},
        {"code": "protoRx", "category": "protocol", "desc": "协议帧接收", "display": []},
    ],
}

PROFILE = {
    "schema": "lstest.project-markers.v1",
    "event_rules": [
        {"rule_id": "proto-rx", "code": "protoRx",
         "regex": r"A5\s+FA\s+(?P<frame>[0-9A-F]{2}(?:\s+[0-9A-F]{2})*)"},
        {"rule_id": "never-hit", "code": "volumeSet",
         "regex": r"^volume\s*:\s*(?P<value>\d+)$"},
    ],
}

LINES = [
    "[2026-11-25T15:02:24.310+08:00] COM6 A #192 [D][evs_event # mini-player] mini player set vol : 100",
    "[2026-11-25T15:04:05.004+08:00] COM3 C #15 A5 FA 09",
    "[2026-11-25T15:02:29.342+08:00] COM6 A #197 [D][algo_task # config] refresh config volume=4 voice=1 wakeup=1",
    "[2026-11-25T15:02:29.394+08:00] COM6 A #198 [D][algo_task # config] save config success",
    "[2026-11-25T15:02:24.310+08:00] COM6 A #192 [D][evs_event # mini-player] mini player set vol : 79",
    "[2026-11-25T15:02:29.342+08:00] COM6 A #197 [D][algo_task # config] refresh config volume=3 voice=1 wakeup=1",
]


class MarkerDiscoveryTests(unittest.TestCase):
    def test_configured_rule_counts_hits(self):
        report = marker_discovery.scan(LINES, {m["code"]: m for m in CATALOG["markers"]}, PROFILE["event_rules"])
        self.assertEqual(report["configured"]["protoRx"]["count"], 1)

    def test_silent_rule_is_reported(self):
        report = marker_discovery.scan(LINES, {m["code"]: m for m in CATALOG["markers"]}, PROFILE["event_rules"])
        silent = {item["code"] for item in report["silent"]}
        self.assertIn("volumeSet", silent)

    def test_unclaimed_lines_are_shaped_and_counted(self):
        report = marker_discovery.scan(LINES, {m["code"]: m for m in CATALOG["markers"]}, PROFILE["event_rules"])
        shapes = " ".join(item["shape"] for item in report["unclaimed"])
        self.assertIn("mini player set vol", shapes)
        self.assertIn("refresh config volume=", shapes)
        self.assertNotIn("A5 FA 09", shapes)

    def test_keyword_match_uses_word_boundaries(self):
        self.assertFalse(marker_discovery._matches_keyword("vad", '{"VadGap":0}'))
        self.assertTrue(marker_discovery._matches_keyword("volumeSet", "volumeSet: 100"))
        self.assertTrue(marker_discovery._matches_keyword("volumeSet", "volume_set 100"))

    def test_cli_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "serial_logs"
            logs.mkdir()
            (logs / "a.log").write_text("\n".join(LINES) + "\n", encoding="utf-8")
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps(CATALOG, ensure_ascii=False), encoding="utf-8")
            profile = root / "profile.json"
            profile.write_text(json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8")
            code = marker_discovery.main([
                "--logs", str(logs), "--catalog", str(catalog),
                "--profile", str(profile), "--json",
            ])
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
