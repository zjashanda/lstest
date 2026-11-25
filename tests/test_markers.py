from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lstest.scripts.markers import (
    MarkerExtractor,
    load_catalog,
    load_rules,
    render_hits,
)

CATALOG = {
    "schema": "lstest.marker-catalog.v1",
    "version": "1.0.0",
    "categories": [{"id": "wake", "label": "唤醒与识别"}],
    "markers": [
        {"code": "wakeup", "category": "wake", "desc": "唤醒", "display": ["keyword", "intentStr"]},
        {"code": "playId", "category": "media", "desc": "提示音 id", "display": []},
        {"code": "md5Check", "category": "burn", "desc": "MD5", "display": []},
    ],
}

PROFILE = {
    "schema_version": 1,
    "profile_id": "demo",
    "event_rules": [
        {
            "rule_id": "demo-ap-wakeup",
            "code": "wakeup",
            "sources": {"sources": ["ap"]},
            "regex": "Wakeup:\\s*(?P<payload>\\{.*\\})",
            "presentation_capture": "payload",
            "stage": "wake",
        },
        {
            "rule_id": "demo-player-play-id",
            "code": "playId",
            "sources": {"sources": ["ap"]},
            "regex": "play id\\s*:\\s*(?P<id>\\d+)",
            "presentation_capture": "id",
        },
        {
            "rule_id": "demo-burn-md5",
            "code": "md5Check",
            "sources": {"sources": ["burn"]},
            "regex": "MD5 CHECK SUCCESS MD5_VALUE\\s*=\\s*(?P<md5>0x[0-9A-Fa-f]+)",
            "presentation_capture": "md5",
        },
        {
            "rule_id": "legacy-rule-without-code",
            "sources": {"sources": ["ap"]},
            "regex": "(bootloader application)",
            "presentation_capture": None,
        },
    ],
}


class MarkerEngineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "markers").mkdir()
        (root / "profiles" / "demo").mkdir(parents=True)
        self.catalog_path = root / "markers" / "catalog.json"
        self.profile_path = root / "profiles" / "demo" / "profile.json"
        self.catalog_path.write_text(json.dumps(CATALOG, ensure_ascii=False), encoding="utf-8")
        self.profile_path.write_text(json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_catalog_and_rules_load(self):
        catalog = load_catalog(self.catalog_path)
        self.assertIn("wakeup", catalog)
        self.assertEqual(catalog["wakeup"].display, ("keyword", "intentStr"))
        rules = load_rules(self.profile_path, catalog)
        self.assertEqual(len(rules), 4)
        self.assertEqual(rules[0].code, "wakeup")
        self.assertEqual(rules[3].code, "legacy-rule-without-code")

    def test_wakeup_renders_keyword_and_intent(self):
        extractor = MarkerExtractor.from_paths(self.catalog_path, self.profile_path)
        line = 'Wakeup: {"istart":1106,"keyword":"tian1 mao1 jing1 ling2","intentStr":"tian1 mao1 jing1 ling2","ncm":176}'
        hits = extractor.match("ap", "COM6", line)
        self.assertEqual(len(hits), 1)
        rendered = render_hits(hits)
        self.assertEqual(rendered, [
            "  ap(COM6): keyword: tian1 mao1 jing1 ling2",
            "  ap(COM6): intentStr: tian1 mao1 jing1 ling2",
        ])

    def test_role_filter_blocks_other_sources(self):
        extractor = MarkerExtractor.from_paths(self.catalog_path, self.profile_path)
        self.assertEqual(extractor.match("burn", "COM6", "Wakeup: {}"), [])
        self.assertEqual(len(extractor.match("burn", "COM15", "MD5 CHECK SUCCESS MD5_VALUE = 0xF6D6")), 1)

    def test_play_id_and_legacy_rule(self):
        extractor = MarkerExtractor.from_paths(self.catalog_path, self.profile_path)
        self.assertEqual(render_hits(extractor.match("ap", "COM6", "[D][mini_td # mini-player] play id : 21")),
                         ["  ap(COM6): playId: 21"])
        legacy = extractor.match("ap", "COM6", ">>>> bootloader application >>>>")
        self.assertEqual(legacy[0].code, "legacy-rule-without-code")

    def test_unmatched_lines_produce_nothing(self):
        extractor = MarkerExtractor.from_paths(self.catalog_path, self.profile_path)
        self.assertEqual(extractor.match("ap", "COM6", "[D][algo_task] unrelated noise"), [])

    def test_report_missing_lists_unconfigured_codes(self):
        catalog = load_catalog(self.catalog_path)
        payload = json.loads(self.profile_path.read_text(encoding="utf-8"))
        payload["event_rules"] = payload["event_rules"][:1]
        self.profile_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        extractor = MarkerExtractor(catalog, load_rules(self.profile_path, catalog))
        missing = extractor.report_missing("ap")
        self.assertIn("playId", missing)
        self.assertIn("md5Check", missing)
        self.assertNotIn("wakeup", missing)


if __name__ == "__main__":
    unittest.main()
