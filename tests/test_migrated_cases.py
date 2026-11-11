"""Data-integrity checks for the migrated ``cases/`` layer.

These tests only validate structure, counts and provenance.  They do not
evaluate assertions and they deliberately assert no thresholds: the case
files must not invent latency or pass-rate numbers that the source docs do
not state.
"""

from __future__ import annotations

import csv
import json
import re
import unittest
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CASES = REPO_ROOT / "cases"
PACKS = CASES / "packs"
EXAMPLES = CASES / "examples"
FA2_DIR = CASES / "fa2"
DOCS = REPO_ROOT / "docs" / "voice-validation"

CASE_SCHEMA = "lstest.case.v1"
SOURCE_COMMIT = "a4ef61478f343e599282ba3aac4cc8705fbdae4a"
PROFILES = {"cskwb01", "venusws63"}
STAGES = {
    "wake",
    "recognition",
    "command",
    "duplex",
    "false_wake",
    "network",
    "attribution",
}
CASE_KEYS = {
    "schema",
    "case_id",
    "title",
    "profile",
    "stage",
    "metadata",
    "assertions",
    "notes",
}
ALLOWED_KINDS = {
    "event_exists",
    "event_absent",
    "event_order",
    "event_within_ms",
    "event_not_during",
    "sequence",
    "duration",
    "response",
    "count_range",
    "state_required",
    "no_reboot",
}
EXPECTED_PACKS = {
    "basic-command",
    "false-wake",
    "first-wake",
    "half-duplex",
    "online-full-duplex",
    "online-mixed-stress",
    "recognition-mode-wake",
}
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def load_json(path: Path):
    # Several Polaris sources carry a UTF-8 BOM; migrated files are plain
    # UTF-8, but utf-8-sig reads both without altering content.
    with path.open(encoding="utf-8-sig") as handle:
        return json.load(handle)


class MigratedCaseContractTest(unittest.TestCase):
    def _case_files(self):
        files = []
        for path in sorted(CASES.rglob("*.json")):
            if FA2_DIR in path.parents:
                continue
            files.append(path)
        return files

    def test_every_case_json_parses(self):
        files = self._case_files()
        self.assertTrue(files, "no case JSON files found")
        for path in files:
            with self.subTest(path=str(path)):
                self.assertIsInstance(load_json(path), dict)

    def test_case_contract_fields(self):
        for path in self._case_files():
            data = load_json(path)
            with self.subTest(path=str(path)):
                self.assertEqual(set(data), CASE_KEYS)
                self.assertEqual(data["schema"], CASE_SCHEMA)
                self.assertIsInstance(data["case_id"], str)
                self.assertTrue(data["case_id"])
                self.assertIsInstance(data["title"], str)
                self.assertTrue(data["title"])
                self.assertIn(data["profile"], PROFILES)
                self.assertIn(data["stage"], STAGES)
                self.assertIsInstance(data["assertions"], list)
                self.assertIsInstance(data["notes"], list)
                self.assertTrue(data["notes"], "notes must record source wording")

    def test_metadata_and_provenance(self):
        for path in self._case_files():
            data = load_json(path)
            with self.subTest(path=str(path)):
                metadata = data["metadata"]
                self.assertEqual(set(metadata), {"timeline", "polaris_source"})
                timeline = metadata["timeline"]
                self.assertEqual(
                    set(timeline), {"required_facts", "mirror", "duplicate"}
                )
                self.assertIsInstance(timeline["required_facts"], list)
                self.assertTrue(timeline["mirror"])
                self.assertTrue(timeline["duplicate"])
                source = metadata["polaris_source"]
                self.assertEqual(set(source), {"task", "path", "commit"})
                self.assertTrue(source["task"])
                self.assertTrue(source["path"])
                self.assertTrue(source["commit"])
                self.assertRegex(source["commit"], COMMIT_RE)
                self.assertEqual(source["commit"], SOURCE_COMMIT)
                self.assertTrue(
                    any(note.startswith("来源：") for note in data["notes"]),
                    "notes must include a 来源： line",
                )

    def test_assertion_kinds_are_allowed(self):
        for path in self._case_files():
            data = load_json(path)
            names = set()
            for index, assertion in enumerate(data["assertions"]):
                with self.subTest(path=str(path), index=index):
                    self.assertIsInstance(assertion, dict)
                    self.assertTrue(assertion.get("name"))
                    self.assertIn(assertion.get("kind"), ALLOWED_KINDS)
                    self.assertNotIn(assertion["name"], names)
                    names.add(assertion["name"])

    def test_case_ids_are_unique(self):
        seen = {}
        for path in self._case_files():
            data = load_json(path)
            case_id = data["case_id"]
            with self.subTest(case_id=case_id):
                self.assertNotIn(case_id, seen, f"duplicate case_id in {seen.get(case_id)}")
                seen[case_id] = path

    def test_pack_and_example_counts(self):
        pack_files = sorted(PACKS.glob("*.json"))
        example_files = sorted(EXAMPLES.glob("*.json"))
        self.assertEqual(len(pack_files), 7)
        self.assertEqual(len(example_files), 29)
        self.assertEqual({path.stem for path in pack_files}, EXPECTED_PACKS)


class Fa2ParityTest(unittest.TestCase):
    JSON_PATH = FA2_DIR / "fa2-command-beep-expectation.json"
    CSV_PATH = FA2_DIR / "fa2-command-beep-expectation.csv"

    def test_row_and_count_parity(self):
        data = load_json(self.JSON_PATH)
        self.assertEqual(data["command_count"], 343)
        rows = data["rows"]
        self.assertEqual(len(rows), 343)
        self.assertEqual(len(rows), data["command_count"])

        for field in ("command_kind", "beep_expectation", "noop_expectation"):
            with self.subTest(field=field):
                self.assertEqual(dict(Counter(row[field] for row in rows)), data["counts"][field])

        case_ids = [row["case_id"] for row in rows]
        self.assertEqual(len(case_ids), len(set(case_ids)))

    def test_csv_matches_json_rows(self):
        data = load_json(self.JSON_PATH)
        with self.CSV_PATH.open(encoding="utf-8-sig", newline="") as handle:
            csv_rows = list(csv.DictReader(handle))
        self.assertEqual(len(csv_rows), len(data["rows"]))
        self.assertEqual(
            [row["case_id"] for row in csv_rows],
            [row["case_id"] for row in data["rows"]],
        )
        self.assertEqual(
            [int(row["index"]) for row in csv_rows],
            list(range(1, len(csv_rows) + 1)),
        )


class MigratedDocsProvenanceTest(unittest.TestCase):
    def test_every_migrated_markdown_starts_with_provenance(self):
        files = sorted(DOCS.rglob("*.md"))
        self.assertTrue(files, "no migrated docs found")
        for path in files:
            with self.subTest(path=str(path)):
                with path.open(encoding="utf-8-sig") as handle:
                    first_line = handle.readline().rstrip("\n")
                self.assertTrue(
                    first_line.startswith("<!-- migrated-from:"),
                    f"{path} first line is not a migrated-from marker: {first_line!r}",
                )


if __name__ == "__main__":
    unittest.main()
