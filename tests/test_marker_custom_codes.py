from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lstest.scripts import markers, profile


CATALOG = {
    "schema": "lstest.marker-catalog.v1",
    "version": "test",
    "markers": [
        {"code": "volumeLevel", "category": "player", "desc": "闊抽噺妗ｄ綅", "log_policy": "change",
         "aliases": ["闊抽噺妗ｄ綅", "闊抽噺绛夌骇"], "value_semantics": "0-4", "sample": "refresh config volume=4"},
    ],
}


def write(tmp: Path, name: str, payload: dict) -> Path:
    path = tmp / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def profile_payload(rules: list[dict], custom: dict | None = None) -> dict:
    payload = {
        "schema_version": 1,
        "profile_id": "t",
        "contract_mode": "draft",
        "event_rules": rules,
    }
    if custom is not None:
        payload["custom_codes"] = custom
    return payload


class CustomCodeKeepsNameTest(unittest.TestCase):
    def test_custom_code_is_not_renamed_to_rule_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = markers.load_catalog(write(root, "catalog.json", CATALOG))
            prof = write(root, "profile.json", profile_payload(
                [{"rule_id": "duya-pos", "code": "curtainPos",
                  "regex": "curtain pos=(?P<pos>\\d+)", "presentation_capture": "pos"}],
                {"curtainPos": {"desc": "绐楀笜浣嶇疆", "value_semantics": "0-100", "sample": "curtain pos=42"}},
            ))
            rules = markers.load_rules(prof, catalog)
            self.assertEqual(rules[0].code, "curtainPos")
            self.assertTrue(rules[0].custom)

    def test_catalog_code_is_not_custom(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = markers.load_catalog(write(root, "catalog.json", CATALOG))
            prof = write(root, "profile.json", profile_payload(
                [{"rule_id": "vol", "code": "volumeLevel",
                  "regex": "config volume=(?P<v>\\d)", "presentation_capture": "v"}],
            ))
            rules = markers.load_rules(prof, catalog)
            self.assertEqual(rules[0].code, "volumeLevel")
            self.assertFalse(rules[0].custom)


class CustomCodeDefinitionRequiredTest(unittest.TestCase):
    def test_missing_definition_raises_contract_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = markers.load_catalog(write(root, "catalog.json", CATALOG))
            prof = write(root, "profile.json", profile_payload(
                [{"rule_id": "r1", "code": "mystery", "regex": "x=(?P<v>\\d)", "presentation_capture": "v"}],
                {"mystery": {"desc": "鍙湁鎻忚堪"}},
            ))
            with self.assertRaises(markers.MarkerContractError) as ctx:
                markers.load_rules(prof, catalog)
            self.assertIn("BLOCKED_PROFILE_CONTRACT", str(ctx.exception))

    def test_inline_definition_on_rule_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = markers.load_catalog(write(root, "catalog.json", CATALOG))
            prof = write(root, "profile.json", profile_payload(
                [{"rule_id": "r2", "code": "curtainPos", "regex": "pos=(?P<v>\\d)",
                  "presentation_capture": "v", "desc": "绐楀笜浣嶇疆",
                  "value_semantics": "0-100", "sample": "pos=42"}],
            ))
            rules = markers.load_rules(prof, catalog)
            self.assertEqual(rules[0].code, "curtainPos")


class ProfileCustomCodesValidationTest(unittest.TestCase):
    def _load(self, payload: dict):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(Path(tmp), "profile.json", payload)
            return profile.DeviceProfile.load(path)

    def test_profile_rejects_incomplete_custom_code(self):
        payload = profile_payload([], {"mystery": {"desc": "鍙湁鎻忚堪"}})
        with self.assertRaises(profile.ProfileError) as ctx:
            self._load(payload)
        self.assertIn("BLOCKED_PROFILE_CONTRACT", str(ctx.exception))

    def test_profile_accepts_complete_custom_code(self):
        payload = profile_payload([], {
            "curtainPos": {"desc": "绐楀笜浣嶇疆", "value_semantics": "0-100", "sample": "pos=42"}
        })
        loaded = self._load(payload)
        self.assertEqual(loaded.profile_id, "t")


class AliasesAndReportTest(unittest.TestCase):
    def test_catalog_aliases_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = markers.load_catalog(write(Path(tmp), "catalog.json", CATALOG))
            spec = catalog["volumeLevel"]
            self.assertIn("闊抽噺妗ｄ綅", spec.aliases)
            self.assertEqual(spec.value_semantics, "0-4")

    def test_custom_report_lists_custom_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = markers.load_catalog(write(root, "catalog.json", CATALOG))
            prof = write(root, "profile.json", profile_payload(
                [{"rule_id": "r3", "code": "curtainPos", "regex": "pos=(?P<v>\\d)",
                  "presentation_capture": "v", "project_note": "鏉滀簹涓撴湁"}],
                {"curtainPos": {"desc": "绐楀笜浣嶇疆", "value_semantics": "0-100", "sample": "pos=42"}},
            ))
            extractor = markers.MarkerExtractor(catalog, markers.load_rules(prof, catalog))
            self.assertEqual(extractor.custom, ["curtainPos"])
            report = extractor.custom_report()
            self.assertEqual(report[0]["code"], "curtainPos")
            self.assertEqual(report[0]["note"], "鏉滀簹涓撴湁")

    def test_report_missing_is_informational(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = markers.load_catalog(write(root, "catalog.json", CATALOG))
            prof = write(root, "profile.json", profile_payload([]))
            extractor = markers.MarkerExtractor(catalog, markers.load_rules(prof, catalog))
            self.assertEqual(extractor.report_missing("cp"), ["volumeLevel"])


if __name__ == "__main__":
    unittest.main()
