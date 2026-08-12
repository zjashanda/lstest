"""Versioned project profile loading and command policy helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

try:
    from .core import sha256_file
except ImportError:  # direct execution fallback
    from core import sha256_file


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class DeviceProfile:
    profile_id: str
    schema_version: int
    source: Path
    payload: Mapping[str, Any]
    sha256: str

    @classmethod
    def load(cls, path: Path) -> "DeviceProfile":
        path = Path(path).resolve()
        if not path.is_file():
            raise ProfileError(f"profile not found: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProfileError(f"invalid profile: {error}") from error
        if not isinstance(payload, Mapping):
            raise ProfileError("profile root must be an object")
        profile_id = str(payload.get("profile_id") or payload.get("device_id") or "").strip()
        if not profile_id:
            raise ProfileError("profile_id is required")
        schema_version = int(payload.get("schema_version", 0))
        if schema_version <= 0:
            raise ProfileError("schema_version must be positive")
        if not isinstance(payload.get("ports", []), list):
            raise ProfileError("ports must be a list")
        if not isinstance(payload.get("commands", []), list):
            raise ProfileError("commands must be a list")
        if "wake_words" in payload and not isinstance(payload["wake_words"], list):
            raise ProfileError("wake_words must be a list")
        for item in payload.get("commands", []):
            if not isinstance(item, Mapping) or not str(item.get("command", "")).strip():
                raise ProfileError("each command rule requires command")
            for name in ("success_patterns", "evidence_patterns"):
                if name in item and not isinstance(item[name], list):
                    raise ProfileError(f"command {name} must be a list")
            for name in ("retries",):
                if name in item and (not isinstance(item[name], int) or item[name] < 0):
                    raise ProfileError(f"command {name} must be a non-negative integer")
            for name in ("timeout_s", "evidence_timeout_s", "retry_delay_s"):
                if name in item:
                    try:
                        if float(item[name]) <= 0:
                            raise ValueError
                    except (TypeError, ValueError) as error:
                        raise ProfileError(f"command {name} must be positive") from error
            if item.get("safe_init", True) and not item.get("success_patterns") and not item.get("evidence_patterns"):
                raise ProfileError("safe initialization command requires success_patterns or evidence_patterns")
        for field in ("correlation", "player_markers", "observations", "recovery"):
            if field in payload and not isinstance(payload[field], Mapping):
                raise ProfileError(f"{field} must be an object")
        recovery = payload.get("recovery", {})
        if isinstance(recovery, Mapping):
            if "stop_on_failure" in recovery and not isinstance(recovery["stop_on_failure"], bool):
                raise ProfileError("recovery stop_on_failure must be boolean")
            for name in ("initialization_timeout_s", "restart_poll_interval_s"):
                if name in recovery:
                    try:
                        if float(recovery[name]) <= 0:
                            raise ValueError
                    except (TypeError, ValueError) as error:
                        raise ProfileError(f"recovery {name} must be positive") from error
        wake_word_ids: set[str] = set()
        for item in payload.get("wake_words", []):
            if not isinstance(item, Mapping):
                raise ProfileError("each wake_words item must be an object")
            wake_word_id = str(item.get("wake_word_id") or "").strip()
            spoken_text = str(item.get("spoken_text") or "").strip()
            expected_raw = item.get("expected_raw")
            if not wake_word_id or not spoken_text or not isinstance(expected_raw, Mapping) or not expected_raw:
                raise ProfileError("each wake_words item requires wake_word_id, spoken_text, and expected_raw")
            if wake_word_id in wake_word_ids:
                raise ProfileError(f"duplicate wake_word_id: {wake_word_id}")
            wake_word_ids.add(wake_word_id)
        return cls(profile_id, schema_version, path, payload, sha256_file(path))

    @property
    def ports(self) -> list[Mapping[str, Any]]:
        return list(self.payload.get("ports", []))

    @property
    def initialization_patterns(self) -> list[str]:
        return [str(item) for item in self.payload.get("initialization_patterns", [])]

    @property
    def restart_patterns(self) -> list[str]:
        return [str(item) for item in self.payload.get("restart_patterns", [])]

    @property
    def correlation(self) -> Mapping[str, Any]:
        value = self.payload.get("correlation", {})
        return value if isinstance(value, Mapping) else {}

    @property
    def player_markers(self) -> Mapping[str, Any]:
        value = self.payload.get("player_markers", {})
        return value if isinstance(value, Mapping) else {}

    @property
    def observations(self) -> Mapping[str, Any]:
        value = self.payload.get("observations", {})
        return value if isinstance(value, Mapping) else {}

    @property
    def recovery(self) -> Mapping[str, Any]:
        value = self.payload.get("recovery", {})
        return value if isinstance(value, Mapping) else {}

    @property
    def wake_words(self) -> list[Mapping[str, Any]]:
        """Ordered wakeword requirements; each test must select one entry explicitly."""
        return [dict(item) for item in self.payload.get("wake_words", []) if isinstance(item, Mapping)]

    def observation_rules(self, category: str) -> Mapping[str, Any]:
        value = self.observations.get(category, {})
        return value if isinstance(value, Mapping) else {}

    def command_rule(self, command: str, role: str | None = None) -> Mapping[str, Any] | None:
        for item in self.payload.get("commands", []):
            if not isinstance(item, Mapping) or item.get("command") != command:
                continue
            allowed_roles = item.get("roles") or []
            if role is None or not allowed_roles or role in allowed_roles:
                return item
        return None

    def assert_command_allowed(self, command: str, role: str | None = None) -> Mapping[str, Any]:
        if any(token in command for token in ("\n", "\r", ";", "&&", "||")):
            raise ProfileError("command chaining is not allowed")
        rule = self.command_rule(command, role)
        if rule is None:
            raise ProfileError(f"command is not allowed by profile: {command}")
        return rule

    def match_any(self, patterns: list[str], text: str) -> bool:
        return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)

    def capability_for_role(self, role: str | None) -> set[str]:
        for item in self.ports:
            if item.get("role") == role:
                return {str(value) for value in item.get("capabilities", [])}
        return set()

    def infer_role(self, text: str) -> tuple[str | None, float]:
        candidates: list[tuple[str, float]] = []
        for item in self.ports:
            role = str(item.get("role", "")).strip()
            patterns = [str(value) for value in item.get("inference_patterns", [])]
            if role and patterns and self.match_any(patterns, text):
                candidates.append((role, 1.0))
        if len(candidates) == 1:
            return candidates[0]
        return None, 0.0
