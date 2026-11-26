from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lstest.scripts import profile_merge


def make_profile(path: Path, rules: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": "lstest.project-markers.v1", "event_rules": rules},
                               ensure_ascii=False), encoding="utf-8")
    return path


class ProfileMergeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "profile.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_adds_new_rule_and_keeps_existing(self):
        make_profile(self.path, [{"rule_id": "a", "code": "intent", "regex": "x"}])
        result = profile_merge.merge(self.path, [{"rule_id": "b", "code": "wakeup", "regex": "y"}])
        self.assertEqual(result["added"], ["b"])
        ids = [r["rule_id"] for r in profile_merge.load(self.path)["event_rules"]]
        self.assertEqual(sorted(ids), ["a", "b"])

    def test_updates_same_rule_id(self):
        make_profile(self.path, [{"rule_id": "a", "code": "intent", "regex": "old"}])
        result = profile_merge.merge(self.path, [{"rule_id": "a", "code": "intent", "regex": "new"}])
        self.assertEqual(result["updated"], ["a"])
        rules = profile_merge.load(self.path)["event_rules"]
        self.assertEqual(rules[0]["regex"], "new")

    def test_backup_created_before_write(self):
        make_profile(self.path, [{"rule_id": "a", "code": "intent", "regex": "x"}])
        result = profile_merge.merge(self.path, [{"rule_id": "b", "code": "wakeup", "regex": "y"}])
        self.assertTrue(result["backup"])
        self.assertTrue(Path(result["backup"]).is_file())

    def test_merge_never_removes_rules(self):
        make_profile(self.path, [
            {"rule_id": "a", "code": "intent", "regex": "x"},
            {"rule_id": "b", "code": "wakeup", "regex": "y"},
        ])
        result = profile_merge.merge(self.path, [{"rule_id": "c", "code": "heap", "regex": "z"}])
        self.assertEqual(result["total"], 3)
        ids = {r["rule_id"] for r in profile_merge.load(self.path)["event_rules"]}
        self.assertEqual(ids, {"a", "b", "c"})

    def test_explicit_remove_requires_flag(self):
        make_profile(self.path, [{"rule_id": "a", "code": "intent", "regex": "x"}])
        with self.assertRaises(profile_merge.ProfileRegression):
            profile_merge.merge(self.path, [{"rule_id": "a", "code": "intent", "regex": "x"}],
                                allow_remove=False, drop=["a"])

    def test_guard_detects_shrink(self):
        make_profile(self.path, [{"rule_id": "a", "code": "intent", "regex": "x"}])
        with self.assertRaises(profile_merge.ProfileRegression):
            profile_merge.guard(self.path, previous_count=5)

    def test_diff_against_backup(self):
        make_profile(self.path, [{"rule_id": "a", "code": "intent", "regex": "x"}])
        profile_merge.merge(self.path, [{"rule_id": "b", "code": "wakeup", "regex": "y"}])
        diff = profile_merge.diff_rules(self.path)
        self.assertIn("b", diff["added"])
        self.assertEqual(diff["removed"], [])


if __name__ == "__main__":
    unittest.main()
