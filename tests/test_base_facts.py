from __future__ import annotations

import unittest

from lstest.scripts.base_facts import BASE_INTERACTION_FACTS, missing_base_fact_events
from lstest.scripts.profile import EVENT_REGISTRY
from lstest.scripts.timeline import FACT_TAGS


class BaseFactTests(unittest.TestCase):
    def test_missing_and_not_applicable(self):
        gaps = missing_base_fact_events({"wake_result"})
        self.assertIn("offline_asr", gaps)
        self.assertNotIn("wake", gaps)
        gaps = missing_base_fact_events(
            {"wake_result"},
            not_applicable={"offline_asr", "online_tts"},
        )
        self.assertNotIn("offline_asr", gaps)
        self.assertNotIn("online_tts", gaps)

    def test_event_map_can_override_canonical_event_type(self):
        gaps = missing_base_fact_events(
            {"player_state"},
            event_map={"offline_tts": "player_state"},
        )
        self.assertNotIn("offline_tts", gaps)

    def test_tts_base_events_are_registered(self):
        for event_type in ("tts_offline", "tts_online", "tts_id"):
            self.assertIn(event_type, EVENT_REGISTRY)
        for key in ("TTS_OFFLINE", "TTS_ONLINE", "TTS_ID"):
            self.assertIn(key, FACT_TAGS)

    def test_base_fact_map_has_interaction_nodes(self):
        for fact_id in ("wake", "offline_asr", "online_request", "online_tts", "tts_id", "restart"):
            self.assertIn(fact_id, BASE_INTERACTION_FACTS)


if __name__ == "__main__":
    unittest.main()
