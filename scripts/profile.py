"""Versioned project profile loading and command policy helpers."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

try:
    from .core import sha256_file
except ImportError:  # direct execution fallback
    from core import sha256_file


class ProfileError(ValueError):
    pass


TOOL_LOG_FACT_KEYS = {
    "WAKE", "OFFLINE_TONE", "OFFLINE_ASR", "OFFLINE_INTENT", "OFFLINE_TEXT",
    "OFFLINE_NORMALIZED", "ONLINE_ASR", "REQUEST_ID", "RESPONSE_ID",
    "ASR_LATENCY_MS", "PLAY_URL", "AUDIO_FILE", "BROADCAST_ID", "PLAYER",
    "INIT_WAIT", "INIT_READY", "INIT_STABLE", "RESTART", "LOG_LEVEL", "COMMAND",
}


class MarkerGate:
    """Profile-scoped marker matcher with scope and debounce protection."""

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}

    @staticmethod
    def _items(value: Any) -> set[str]:
        if value is None or value == "":
            return set()
        return {str(item) for item in (value if isinstance(value, (list, tuple, set)) else [value])}

    def match(
        self,
        rule: str | Mapping[str, Any],
        text: str,
        *,
        port: str | None = None,
        role: str | None = None,
        phase: str | None = None,
        correlation_id: str | None = None,
        monotonic_seconds: float | None = None,
    ) -> tuple[bool, str]:
        if isinstance(rule, str):
            matched = bool(re.search(rule, text, flags=re.IGNORECASE))
            return matched, "matched" if matched else "pattern_not_matched"
        if not isinstance(rule, Mapping):
            return False, "invalid_rule"
        pattern = str(rule.get("pattern") or rule.get("regex") or "")
        if not pattern:
            return False, "pattern_missing"
        if not re.search(pattern, text, flags=re.IGNORECASE):
            return False, "pattern_not_matched"
        if self._items(rule.get("ports")) and str(port or "") not in self._items(rule.get("ports")):
            return False, "port_scope_rejected"
        if self._items(rule.get("roles")) and str(role or "") not in self._items(rule.get("roles")):
            return False, "role_scope_rejected"
        if self._items(rule.get("phases")) and str(phase or "") not in self._items(rule.get("phases")):
            return False, "phase_scope_rejected"
        required_id = str(rule.get("correlation_id") or "")
        if required_id and required_id != str(correlation_id or ""):
            return False, "correlation_scope_rejected"
        if any(re.search(str(item), text, flags=re.IGNORECASE) for item in rule.get("negative_patterns", [])):
            return False, "negative_pattern_matched"
        debounce_ms = max(0, int(rule.get("debounce_ms", 0) or 0))
        if debounce_ms and monotonic_seconds is not None:
            fingerprint = str(rule.get("fingerprint") or f"{pattern}|{port}|{role}|{correlation_id}|{text}")
            now = monotonic_seconds
            previous = self._seen.get(fingerprint)
            self._seen[fingerprint] = now
            # A predicate and its subsequent event filter commonly inspect the
            # same captured serial line.  Do not let that second inspection
            # consume the marker as a new debounce event.
            if previous == now:
                return True, "matched_same_event"
            if previous is not None and (now - previous) * 1000 < debounce_ms:
                return False, "debounced"
        return True, "matched"


def _rule_pattern(rule: str | Mapping[str, Any]) -> str:
    if isinstance(rule, str):
        return rule
    if isinstance(rule, Mapping):
        return str(rule.get("pattern") or rule.get("regex") or "")
    return ""


@dataclass(frozen=True)
class DeviceProfile:
    profile_id: str
    schema_version: int
    source: Path
    payload: Mapping[str, Any]
    sha256: str
    marker_gate: MarkerGate

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
        raw_observations = payload.get("observations", {}) or {}
        if not isinstance(raw_observations, Mapping):
            raise ProfileError("observations must be an object")
        for category, value in raw_observations.items():
            if not isinstance(value, Mapping):
                continue
            fact_map = value.get("fact_map", {})
            if not isinstance(fact_map, Mapping):
                raise ProfileError(f"observations.{category}.fact_map must be an object")
            for fact_key in fact_map.values():
                if str(fact_key).upper() not in TOOL_LOG_FACT_KEYS:
                    raise ProfileError(f"unsupported fact_map key: {fact_key}")
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
        for field in ("correlation", "player_markers", "observations", "recovery", "health_policy", "marker_fixtures"):
            if field in payload and not isinstance(payload[field], Mapping):
                raise ProfileError(f"{field} must be an object")
        recovery = payload.get("recovery", {})
        if isinstance(recovery, Mapping):
            if "stop_on_failure" in recovery and not isinstance(recovery["stop_on_failure"], bool):
                raise ProfileError("recovery stop_on_failure must be boolean")
            for name in ("initialization_timeout_s", "restart_poll_interval_s", "stable_for_s"):
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
        cls._validate_marker_rules(payload)
        return cls(profile_id, schema_version, path, payload, sha256_file(path), MarkerGate())

    @staticmethod
    def _validate_marker_rules(payload: Mapping[str, Any]) -> None:
        def validate(rule: Any, location: str) -> None:
            if isinstance(rule, str):
                try:
                    re.compile(rule, flags=re.IGNORECASE)
                except re.error as error:
                    raise ProfileError(f"invalid marker regex at {location}: {error}") from error
                return
            if not isinstance(rule, Mapping):
                raise ProfileError(f"marker rule at {location} must be a string or object")
            pattern = str(rule.get("pattern") or rule.get("regex") or "")
            if not pattern:
                raise ProfileError(f"marker rule at {location} requires pattern")
            try:
                re.compile(pattern, flags=re.IGNORECASE)
                for negative in rule.get("negative_patterns", []):
                    re.compile(str(negative), flags=re.IGNORECASE)
            except re.error as error:
                raise ProfileError(f"invalid marker regex at {location}: {error}") from error
            if "debounce_ms" in rule and int(rule["debounce_ms"]) < 0:
                raise ProfileError(f"marker debounce_ms at {location} must be non-negative")
            fact_map = rule.get("fact_map", {})
            if fact_map and not isinstance(fact_map, Mapping):
                raise ProfileError(f"marker fact_map at {location} must be an object")
            for fact_key in fact_map.values() if isinstance(fact_map, Mapping) else ():
                if str(fact_key).upper() not in TOOL_LOG_FACT_KEYS:
                    raise ProfileError(f"unsupported fact_map key at {location}: {fact_key}")
            fixtures = rule.get("fixtures", {})
            if fixtures and not isinstance(fixtures, Mapping):
                raise ProfileError(f"marker fixtures at {location} must be an object")
            for sample in fixtures.get("positive", []) if isinstance(fixtures, Mapping) else []:
                if not re.search(pattern, str(sample), flags=re.IGNORECASE):
                    raise ProfileError(f"marker positive fixture does not match at {location}")
            for sample in fixtures.get("negative", []) if isinstance(fixtures, Mapping) else []:
                if re.search(pattern, str(sample), flags=re.IGNORECASE):
                    raise ProfileError(f"marker negative fixture matches at {location}")
            for chunks in fixtures.get("segmented", []) if isinstance(fixtures, Mapping) else []:
                joined = "".join(str(item) for item in chunks)
                if not re.search(pattern, joined, flags=re.IGNORECASE):
                    raise ProfileError(f"marker segmented fixture does not match at {location}")

        for field in ("initialization_patterns", "restart_patterns"):
            for index, rule in enumerate(payload.get(field, [])):
                validate(rule, f"{field}[{index}]")
        player_markers = payload.get("player_markers", {})
        if isinstance(player_markers, Mapping):
            for name, value in player_markers.items():
                rules = value if isinstance(value, list) else [value]
                for index, rule in enumerate(rules):
                    if isinstance(rule, (str, Mapping)):
                        validate(rule, f"player_markers.{name}[{index}]")
        observations = payload.get("observations", {})
        if isinstance(observations, Mapping):
            for category, value in observations.items():
                if not isinstance(value, Mapping):
                    continue
                for name in ("patterns", "final_patterns"):
                    for index, rule in enumerate(value.get(name, [])):
                        validate(rule, f"observations.{category}.{name}[{index}]")

    @property
    def ports(self) -> list[Mapping[str, Any]]:
        return list(self.payload.get("ports", []))

    @property
    def initialization_patterns(self) -> list[Any]:
        return list(self.payload.get("initialization_patterns", []))

    @property
    def restart_patterns(self) -> list[Any]:
        return list(self.payload.get("restart_patterns", []))

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
    def health_policy(self) -> Mapping[str, Any]:
        value = self.payload.get("health_policy", {})
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

    def match_any(
        self,
        patterns: list[Any],
        text: str,
        *,
        port: str | None = None,
        role: str | None = None,
        phase: str | None = None,
        correlation_id: str | None = None,
        monotonic_seconds: float | None = None,
    ) -> bool:
        return any(
            self.marker_gate.match(
                rule, text, port=port, role=role, phase=phase,
                correlation_id=correlation_id, monotonic_seconds=monotonic_seconds,
            )[0]
            for rule in patterns
        )

    def match_details(self, patterns: list[Any], text: str, **scope: Any) -> list[tuple[Any, bool, str]]:
        """Return every rule decision so adapters can log scope rejections."""
        return [
            (rule, *self.marker_gate.match(rule, text, **scope))
            for rule in patterns
        ]

    def match_and_extract(self, patterns: list[Any], text: str, **scope: Any) -> list[dict[str, Any]]:
        """匹配项目规则并返回内部提取结果，正式日志不暴露规则文本。

        规则可以使用命名分组，并可通过 ``fact_map`` 将分组映射到框架事实
        key，例如 ``{"keyword": "WAKE"}``。该接口只负责事实提取和作用域
        校验，判定与人读日志由公共 runtime 完成。
        """
        results: list[dict[str, Any]] = []
        for index, rule in enumerate(patterns):
            pattern = _rule_pattern(rule)
            if not pattern:
                results.append({"rule_id": f"rule-{index}", "matched": False, "reason": "pattern_missing"})
                continue
            matched, reason = self.marker_gate.match(rule, text, **scope)
            item: dict[str, Any] = {
                "rule_id": str(rule.get("rule_id") if isinstance(rule, Mapping) else "") or f"rule-{index}",
                "matched": matched,
                "reason": reason,
                "matched_text": "",
                "captures": {},
                "facts": {},
                "evidence": scope.get("evidence", ""),
            }
            if matched:
                match = re.search(pattern, text, flags=re.IGNORECASE)
                if match is not None:
                    item["matched_text"] = match.group(0)
                    item["captures"] = dict(match.groupdict())
                    if not item["captures"]:
                        item["captures"] = {str(pos + 1): value for pos, value in enumerate(match.groups())}
                    mapping = rule.get("fact_map", {}) if isinstance(rule, Mapping) else {}
                    if isinstance(mapping, Mapping):
                        for group_name, fact_key in mapping.items():
                            value = item["captures"].get(str(group_name), "")
                            if value not in (None, ""):
                                item["facts"][str(fact_key).upper()] = value
            results.append(item)
        return results

    def marker_match_reason(self, rule: Any, text: str, **scope: Any) -> str:
        """Expose rejected/debounced marker reasons for project adapter logging."""
        return self.marker_gate.match(rule, text, **scope)[1]

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
