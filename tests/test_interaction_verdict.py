from __future__ import annotations

import unittest

from lstest.scripts.interaction_acceptance import MODES, round_verdict, run_verdict
from lstest.scripts.verdict import VerdictStatus


PLAYBACK = {
    "wake_playback_count": 1,
    "wake_playback_complete": True,
    "asr_response_count": 1,
    "asr_playback_count": 1,
    "asr_playback_complete": True,
}


class InteractionVerdictTests(unittest.TestCase):
    def test_all_acceptance_modes_map_success_to_pass(self):
        self.assertEqual(len(MODES), 8)
        for mode in MODES:
            with self.subTest(mode=mode):
                verdict = round_verdict("hit", mode, **PLAYBACK)
                self.assertIs(verdict.status, VerdictStatus.PASS)
                self.assertEqual(verdict.layers[0].layer_id, "functional")
                self.assertEqual(verdict.layers[0].owner, "test-design")

    def test_asleep_fails_with_anomaly(self):
        verdict = round_verdict("asleep", "wake_only")
        self.assertIs(verdict.status, VerdictStatus.FAIL)
        self.assertIn("ASLEEP", verdict.anomaly_codes)

    def test_strict_mode_rejects_misheard(self):
        verdict = round_verdict("misheard", "strict")
        self.assertIs(verdict.status, VerdictStatus.FAIL)

    def test_any_asr_rejects_empty_result(self):
        verdict = round_verdict("empty", "any_asr")
        self.assertIs(verdict.status, VerdictStatus.FAIL)

    def test_asr_optional_allows_empty_round_but_fails_empty_run(self):
        round_result = round_verdict("empty", "asr_optional")
        self.assertIs(round_result.status, VerdictStatus.PASS)
        run_result = run_verdict(["empty"], "asr_optional")
        self.assertIs(run_result.status, VerdictStatus.FAIL)
        self.assertIn("ASR_OPTIONAL_EMPTY", run_result.anomaly_codes)

    def test_strict_mode_rejects_multi_result(self):
        verdict = round_verdict("hit", "strict", multi_result=True)
        self.assertIs(verdict.status, VerdictStatus.FAIL)
        self.assertIn("MULTI_RESULT", verdict.anomaly_codes)

    def test_missing_wake_playback_is_reported(self):
        verdict = round_verdict("hit", "wake_playback")
        self.assertIs(verdict.status, VerdictStatus.FAIL)
        self.assertIn("WAKE_PLAYBACK_MISSING", verdict.anomaly_codes)

    def test_missing_asr_playback_is_reported(self):
        verdict = round_verdict(
            "hit",
            "strict_playback",
            wake_playback_count=1,
            wake_playback_complete=True,
            asr_response_count=1,
        )
        self.assertIs(verdict.status, VerdictStatus.FAIL)
        self.assertIn("ASR_PLAYBACK_MISSING", verdict.anomaly_codes)


if __name__ == "__main__":
    unittest.main()
