"""Tests for the thin project record adapters (adapters/<project>/adapter.py).

The adapters must stay collection-only: raw text in, ``RawLogRecord`` out, with
``source`` / ``port`` / ``role`` coming from the profile port map and
``observed_at`` from the line prefix.  No marker parsing, no facts, no files.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lstest.adapters.cskwb01 import adapter as csk
from lstest.adapters.venusws63 import adapter as venus
from lstest.adapters.venusws63 import case_plan
from lstest.scripts.profile import DeviceProfile, RawLogRecord

REPO = Path(__file__).resolve().parents[1]
PROFILES = REPO / "profiles"

#: Physical line 4 is blank on purpose: it must be skipped, not interpreted.
LOG_LINES = (
    "2026-05-27T08:30:00 WAKE(1)\n",
    "2026-05-27T08:30:01 wakeup_callback, keyword: xiao mei xiao mei\n",
    "device ack\n",
    "\n",
    "2026-05-27T08:30:03 soundplayer status: 2\n",
)


class _StubRuntime:
    """Records what the adapter forwards, in order."""

    def __init__(self) -> None:
        self.calls: list[tuple[RawLogRecord, str]] = []

    def submit_raw_record(self, record: RawLogRecord, *, case_id: str):
        self.calls.append((record, case_id))
        return []


class _AdapterTestBase(unittest.TestCase):
    adapter = None
    project = ""
    role = ""
    port = ""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.dir = Path(cls._tmp.name)
        cls.log = cls.dir / f"serial_{cls.port}_{cls.role}.log"
        cls.log.write_text("".join(LOG_LINES), encoding="utf-8")

    def records(self, **kwargs):
        return list(self.adapter.iter_records(self.log, **kwargs))


class Cskwb01AdapterTests(_AdapterTestBase):
    adapter = csk
    project = "cskwb01"
    role = "cp"
    port = "COM12"

    def test_every_item_is_a_raw_record(self):
        found = self.records()
        self.assertEqual(len(found), 4, "blank lines must be skipped")
        for record in found:
            self.assertIsInstance(record, RawLogRecord)

    def test_order_cursor_and_sequence(self):
        found = self.records()
        self.assertEqual([item.cursor for item in found], [1, 2, 3, 5])
        self.assertEqual([item.sequence for item in found], [1, 2, 3, 4])
        self.assertEqual(found[0].text, "2026-05-27T08:30:00 WAKE(1)")
        self.assertEqual(found[2].text, "device ack")

    def test_endpoint_comes_from_the_profile_port_map(self):
        for record in self.records():
            self.assertEqual(record.source, "cp")
            self.assertEqual(record.port, "COM12")
            self.assertEqual(record.role, "cp")

    def test_role_is_inferred_from_the_log_filename(self):
        ap_log = self.dir / "serial_COM14_ap.log"
        ap_log.write_text("".join(LOG_LINES), encoding="utf-8")
        record = next(self.adapter.iter_records(ap_log))
        self.assertEqual((record.source, record.port, record.role), ("ap", "COM14", "ap"))

        asr_log = self.dir / "serial_COM13_asr.log"
        asr_log.write_text("".join(LOG_LINES), encoding="utf-8")
        record = next(self.adapter.iter_records(asr_log))
        self.assertEqual((record.source, record.port, record.role), ("asr", "COM13", "asr"))

    def test_role_argument_overrides_the_filename(self):
        record = next(self.adapter.iter_records(self.log, role="asr"))
        self.assertEqual((record.source, record.port, record.role), ("asr", "COM13", "asr"))

    def test_timestamp_prefix_is_parsed_and_optional(self):
        found = self.records()
        self.assertEqual(found[0].observed_at, "2026-05-27T08:30:00")
        self.assertEqual(found[1].observed_at, "2026-05-27T08:30:01")
        self.assertIsNone(found[2].observed_at, "a line without a timestamp stays None")
        self.assertEqual(found[3].observed_at, "2026-05-27T08:30:03")

    def test_default_phase_and_source_are_stamped(self):
        record = next(self.adapter.iter_records(self.log, default_phase="observe"))
        self.assertEqual(record.phase, "observe")
        self.assertEqual(record.source, "cp", "source follows the declared role by default")

    def test_profile_path_and_device_profile_are_equivalent(self):
        path = PROFILES / self.project / "profile.json"
        profile = DeviceProfile.load(path)
        from_path = [(r.source, r.port, r.role, r.cursor) for r in self.adapter.iter_records(self.log, profile=path)]
        from_object = [(r.source, r.port, r.role, r.cursor) for r in self.adapter.iter_records(self.log, profile=profile)]
        self.assertEqual(from_path, from_object)

    def test_no_fact_objects_are_produced(self):
        for record in self.records():
            self.assertFalse(hasattr(record, "presentation_value"))
            self.assertFalse(hasattr(record, "event_type"))
            self.assertFalse(hasattr(record, "captures"))
            self.assertEqual(record.evidence, (), "adapters never invent evidence paths")

    def test_marker_lines_stay_raw_text(self):
        first = self.records()[0]
        self.assertEqual(first.text, "2026-05-27T08:30:00 WAKE(1)")
        self.assertNotIn("WAKE", (first.source, first.role, first.phase))

    def test_unknown_role_is_rejected_instead_of_guessed(self):
        with self.assertRaises(ValueError):
            list(self.adapter.iter_records(self.log, role="cp2"))
        with self.assertRaises(ValueError):
            list(self.adapter.iter_records(self.dir / "unlabelled.log"))

    def test_profile_rules_match_the_stamped_source(self):
        """The stamped source/port/role must land inside the profile rule scope."""
        profile = DeviceProfile.load(PROFILES / self.project / "profile.json")
        record = self.records()[0]
        facts = profile.extract_facts(record)
        self.assertTrue(facts, "profile rules must match the adapter-stamped endpoint")
        self.assertEqual(facts[0].key, "WAKE")
        self.assertEqual((facts[0].source, facts[0].port, facts[0].role), ("cp", "COM12", "cp"))

    def test_submit_forwards_records_in_order(self):
        runtime = _StubRuntime()
        forwarded = self.adapter.submit(runtime, self.records(), case_id="case-1")
        self.assertEqual(forwarded, 4)
        self.assertEqual([call[0].cursor for call in runtime.calls], [1, 2, 3, 5])
        self.assertEqual({call[1] for call in runtime.calls}, {"case-1"})


class Venusws63AdapterTests(_AdapterTestBase):
    adapter = venus
    project = "venusws63"
    role = "upper"
    port = "COM13"

    def test_log_facts_use_the_upper_observation_port(self):
        for record in self.records():
            self.assertEqual(record.source, "upper")
            self.assertEqual(record.port, "COM13")
            self.assertEqual(record.role, "upper")

    def test_asr_alias_shares_the_upper_port(self):
        asr = next(self.adapter.iter_records(self.log, role="asr"))
        upper = next(self.adapter.iter_records(self.log, role="upper"))
        self.assertEqual(asr.port, upper.port, "asr is an alias of upper in this topology")
        self.assertEqual(asr.source, "asr", "but the declared role name is preserved")

    def test_same_shape_as_the_csk_adapter(self):
        ap_log = self.dir / "serial_COM14_ap.log"
        ap_log.write_text("".join(LOG_LINES), encoding="utf-8")
        record = next(self.adapter.iter_records(ap_log))
        self.assertEqual((record.source, record.port, record.role), ("ap", "COM14", "ap"))

    def test_profile_rules_match_the_stamped_upper_source(self):
        profile = DeviceProfile.load(PROFILES / self.project / "profile.json")
        log = self.dir / "serial_COM13_upper_online.log"
        log.write_text(
            "2026-05-27T08:30:00 online_wakeup, SID: 9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e5f\n",
            encoding="utf-8",
        )
        record = next(self.adapter.iter_records(log))
        facts = profile.extract_facts(record)
        self.assertTrue(facts, "profile rules must match the adapter-stamped endpoint")
        self.assertEqual(facts[0].key, "WAKE")
        self.assertEqual((facts[0].source, facts[0].port), ("upper", "COM13"))

    def test_submit_forwards_to_the_runtime(self):
        runtime = _StubRuntime()
        forwarded = self.adapter.submit(runtime, self.records(), case_id="ws63-case")
        self.assertEqual(forwarded, 4)
        self.assertEqual(runtime.calls[0][0].text, "2026-05-27T08:30:00 WAKE(1)")


class Venusws63CasePlanTests(unittest.TestCase):
    def test_plan_is_declarative_and_ordered(self):
        ids = list(case_plan.iter_case_ids())
        self.assertEqual(
            ids,
            [
                "polaris-task-ws63-com20-coverage-check",
                "polaris-task-ws63-tts-empty-url-attribution",
                "polaris-task-online-mixed-stress-ws63",
            ],
        )

    def test_every_case_declares_playback_actions(self):
        for case_id in case_plan.iter_case_ids():
            actions = case_plan.playback_for(case_id)
            self.assertTrue(actions, f"{case_id} needs at least one playback action")
            for action in actions:
                self.assertTrue(action.utterance.endswith(".wav"))
                self.assertGreaterEqual(action.repeat, 1)

    def test_unknown_case_is_rejected(self):
        with self.assertRaises(KeyError):
            case_plan.playback_for("does-not-exist")

    def test_plan_reflects_the_no_cp_topology(self):
        self.assertEqual(case_plan.TOPOLOGY, "ap_wifi_control_no_cp")
        self.assertEqual(case_plan.OBSERVATION_ROLES, ("ap", "upper"))
        self.assertNotIn("cp", case_plan.OBSERVATION_ROLES)

    def test_plan_carries_no_expected_verdict(self):
        serialized = json.dumps(case_plan.to_dict(), ensure_ascii=False)
        for forbidden in ("assertions", "expected", "PASS", "FAIL", "BLOCKED", "AMBIGUOUS", "threshold"):
            self.assertNotIn(forbidden, serialized)
        # The module must not import the judging engines either.
        self.assertFalse(hasattr(case_plan, "assertions"))
        self.assertFalse(hasattr(case_plan, "attribution"))
        self.assertFalse(hasattr(case_plan, "coverage"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
