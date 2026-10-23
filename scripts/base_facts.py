"""Canonical interaction facts that must be captured before acceptance.

Acceptance modes decide what to validate.  They never decide what to collect.
"""

from __future__ import annotations

from typing import Iterable, Mapping


BASE_INTERACTION_FACTS: Mapping[str, str] = {
    "wake": "wake_result",
    "offline_asr": "offline_result",
    "online_request": "online_request",
    "online_response": "online_response",
    "online_result": "online_result",
    "online_intent": "online_intent",
    "offline_tts": "tts_offline",
    "online_tts": "tts_online",
    "tts_id": "tts_id",
    "player_url": "player_url",
    "player_id": "player_id",
    "player_state": "player_state",
    "initialization": "initialization_ready",
    "restart": "restart",
    "command_ack": "command_ack",
    "command_evidence": "command_evidence",
    "device_exception": "device_exception",
}


def missing_base_fact_events(
    declared_event_types: Iterable[str],
    *,
    not_applicable: Iterable[str] = (),
    event_map: Mapping[str, str] | None = None,
) -> list[str]:
    """Return base fact ids whose event rules are not declared.

    Projects may override a canonical event type when their profile uses a
    different existing event category.  Missing facts are reported, not
    silently ignored; callers decide whether they are warnings or blocking.
    """
    declared = {str(item) for item in declared_event_types}
    skipped = {str(item) for item in not_applicable}
    facts = dict(BASE_INTERACTION_FACTS)
    if event_map:
        facts.update({str(key): str(value) for key, value in event_map.items() if key in BASE_INTERACTION_FACTS})
    return [
        fact_id
        for fact_id, event_type in facts.items()
        if fact_id not in skipped and event_type not in declared
    ]
