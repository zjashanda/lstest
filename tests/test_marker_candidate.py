from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lstest.scripts import marker_candidate, markers


CATALOG = {
    "schema": "lstest.marker-catalog.v1",
    "version": "test",
    "markers": [
        {"code": "volumeLevel", "category": "player", "desc": "音量档位"},
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


class BuildDraftsTest(unittest.TestCase):
    def test_custom_code_becomes_pending_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "duya"
            root.mkdir()
            catalog = write(root, "catalog.json", CATALOG)
            prof = write(root, "profile.json", profile_payload(
                [{"rule_id": "duya-pos", "code": "curtainPos", "regex": "pos=(?P<v>\\d+)",
                  "presentation_capture": "v"}],
                {"curtainPos": {"desc": "窗帘位置", "value_semantics": "0-100 整数",
                                "sample": "curtain pos=42"}},
            ))
            drafts = marker_candidate.build_drafts(prof, catalog)
            self.assertEqual(len(drafts), 1)
            draft = drafts[0]
            self.assertEqual(draft["schema"], "entry.v1")
            self.assertEqual(draft["type"], "knowledge")
            self.assertNotIn("status", draft)
            self.assertNotIn("created", draft)
            self.assertNotIn("hits", draft)
            self.assertNotIn("superseded_by", draft)
            self.assertIn("curtainPos", draft["title"])
            self.assertIn("窗帘位置", draft["claim"])
            evidence = draft["evidence"][0]
            self.assertEqual(evidence["kind"], "file")
            self.assertGreater(evidence["bytes"], 0)
            self.assertEqual(len(evidence["sha256"]), 64)

    def test_catalog_code_produces_no_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = write(root, "catalog.json", CATALOG)
            prof = write(root, "profile.json", profile_payload(
                [{"rule_id": "vol", "code": "volumeLevel", "regex": "volume=(?P<v>\\d)",
                  "presentation_capture": "v"}],
            ))
            self.assertEqual(marker_candidate.build_drafts(prof, catalog), [])

    def test_incomplete_definition_refuses_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = write(root, "catalog.json", CATALOG)
            prof = write(root, "profile.json", profile_payload(
                [{"rule_id": "m1", "code": "mystery", "regex": "x=(?P<v>\\d)",
                  "presentation_capture": "v"}],
                {"mystery": {"desc": "只有描述", "value_semantics": "", "sample": ""}},
            ))
            with self.assertRaises(markers.MarkerContractError):
                marker_candidate.build_drafts(prof, catalog)


class CliTest(unittest.TestCase):
    def test_cli_writes_draft_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "duya"
            root.mkdir()
            catalog = write(root, "catalog.json", CATALOG)
            prof = write(root, "profile.json", profile_payload(
                [{"rule_id": "p1", "code": "curtainPos", "regex": "pos=(?P<v>\\d+)",
                  "presentation_capture": "v"}],
                {"curtainPos": {"desc": "窗帘位置", "value_semantics": "0-100",
                                "sample": "pos=42"}},
            ))
            out = root / "drafts.json"
            code = marker_candidate.main([
                "--profile", str(prof), "--catalog", str(catalog), "--out", str(out),
            ])
            self.assertEqual(code, 0)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(len(data), 1)
            self.assertNotIn("status", data[0])
            self.assertIn("claim", data[0])


if __name__ == "__main__":
    unittest.main()
