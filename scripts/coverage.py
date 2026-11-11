"""Capability matrix and coverage policy evaluation for device profiles.

Capabilities are read from an explicit ``ports[].capabilities`` declaration.
There is no project-name inference here: a profile that does not declare a
capability is reported as degraded instead of guessed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .assertions import coerce_facts

__all__ = [
    "POLICY_SCHEMA",
    "capability_matrix",
    "state_coverage",
    "evaluate_policy",
]

POLICY_SCHEMA = "lstest.coverage_policy.v1"

STATUS_PASS = "PASS"
STATUS_SKIP = "SKIP"
STATUS_BLOCKED = "BLOCKED"
STATUS_DEGRADED = "COVERAGE_DEGRADED"

_DEFAULT_REQUIRED_STATES = ("preparation", "active", "terminal")


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items: Sequence[Any] = (value,)
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = tuple(value)
    else:
        items = (value,)
    return [text for text in (_as_text(item).strip() for item in items) if text]


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _load_payload(profile: Mapping[str, Any] | Path | Any) -> tuple[dict, str]:
    if isinstance(profile, Mapping):
        return dict(profile), _as_text(profile.get("profile_id")).strip()
    if isinstance(profile, (str, Path)):
        path = Path(profile)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("profile JSON root must be an object")
        profile_id = _as_text(payload.get("profile_id") or payload.get("device_id")).strip()
        return dict(payload), profile_id or path.stem
    payload = getattr(profile, "payload", None)
    if isinstance(payload, Mapping):
        profile_id = _as_text(getattr(profile, "profile_id", "")).strip()
        if not profile_id:
            profile_id = _as_text(payload.get("profile_id") or payload.get("device_id")).strip()
        data = dict(payload)
        if profile_id:
            data.setdefault("profile_id", profile_id)
        return data, profile_id
    ports = getattr(profile, "ports", None)
    if ports is not None:
        data = {
            "profile_id": _as_text(getattr(profile, "profile_id", "")).strip(),
            "ports": list(ports),
        }
        return data, data["profile_id"]
    raise TypeError(f"cannot read a profile from {type(profile).__name__}")


def _port_entry(port: Any, index: int) -> tuple[str, list[str], str]:
    if isinstance(port, Mapping):
        role = _as_text(port.get("role") or port.get("port") or f"port{index}").strip()
        capabilities = _as_list(port.get("capabilities"))
        raw_name = _as_text(port.get("port") or "")
    else:
        role = _as_text(getattr(port, "role", "") or getattr(port, "port", "") or f"port{index}").strip()
        capabilities = _as_list(getattr(port, "capabilities", None))
        raw_name = _as_text(getattr(port, "port", ""))
    return role, capabilities, raw_name


def capability_matrix(profile: Mapping[str, Any] | Path | Any) -> dict:
    """Report declared port capabilities and any explicit degradation."""
    payload, profile_id = _load_payload(profile)
    raw_ports = payload.get("ports") or []
    if not isinstance(raw_ports, (list, tuple)):
        raw_ports = []

    ports: dict[str, dict[str, Any]] = {}
    port_names: dict[str, str] = {}
    for index, port in enumerate(raw_ports):
        role, capabilities, raw_name = _port_entry(port, index)
        if not role:
            continue
        entry = ports.setdefault(role, {"capabilities": []})
        entry["capabilities"] = _unique(entry["capabilities"] + capabilities)
        if raw_name and role not in port_names:
            port_names[role] = raw_name

    capabilities: dict[str, dict[str, Any]] = {}
    for role, entry in ports.items():
        for capability in entry["capabilities"]:
            record = capabilities.setdefault(
                capability,
                {"supported": True, "roles": [], "source": "ports[].capabilities"},
            )
            if role not in record["roles"]:
                record["roles"].append(role)

    required_roles = _as_list(payload.get("required_roles"))
    required_capabilities = _as_list(payload.get("required_capabilities"))
    degraded_roles = [role for role in required_roles if role not in ports]
    degraded_roles.extend(role for role, entry in ports.items() if not entry["capabilities"])
    degraded_roles = _unique(degraded_roles)
    degraded_capabilities: list[str] = []
    for capability in required_capabilities:
        if capability not in capabilities:
            capabilities[capability] = {
                "supported": False,
                "roles": [],
                "source": "required_capabilities",
            }
            degraded_capabilities.append(capability)
    degraded = [f"role:{role}" for role in degraded_roles]
    degraded.extend(f"capability:{capability}" for capability in degraded_capabilities)

    return {
        "profile_id": profile_id,
        "ports": ports,
        "port_names": port_names,
        "capabilities": capabilities,
        "degraded": degraded,
        "degraded_roles": degraded_roles,
        "degraded_capabilities": degraded_capabilities,
    }


def _state_counts(facts: Iterable[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for fact in coerce_facts(facts):
        state_class = _as_text(fact.state_class).strip()
        if not state_class:
            continue
        counts[state_class] = counts.get(state_class, 0) + 1
    return dict(sorted(counts.items()))


def state_coverage(
    facts: Iterable[Any],
    *,
    required: Sequence[str] = _DEFAULT_REQUIRED_STATES,
    policy: Mapping[str, Any] | None = None,
) -> dict:
    """Count PLAYER state classes and report required-state coverage."""
    required_states = list(required)
    notes: list[str] = []
    if isinstance(policy, Mapping):
        declared = policy.get("required_states")
        if isinstance(declared, (list, tuple)) and declared:
            required_states = _as_list(declared)
            notes.append("required_states taken from policy")
    counts = _state_counts(facts)
    covered = [state for state in required_states if counts.get(state, 0) > 0]
    missing = [state for state in required_states if counts.get(state, 0) <= 0]
    if not required_states:
        status = STATUS_SKIP
        notes.append("no required states declared")
    elif missing:
        status = STATUS_DEGRADED
        notes.append("missing states: " + ", ".join(missing))
    else:
        status = STATUS_PASS
    return {
        "status": status,
        "covered": covered,
        "missing": missing,
        "counts": counts,
        "notes": notes,
    }


def _empty_policy_result(policy: Mapping[str, Any] | None) -> dict:
    return {
        "schema": _as_text((policy or {}).get("schema")) if isinstance(policy, Mapping) else "",
        "status": STATUS_SKIP,
        "reason": "no_policy",
        "roles": {"full": [], "minimum": []},
        "observed_roles": [],
        "missing_roles": [],
        "missing_minimum": [],
        "missing_non_minimum": [],
        "states": {"required": [], "covered": [], "missing": []},
        "state_counts": {},
        "role_counts": {},
        "notes": ["coverage policy is missing"],
    }


def evaluate_policy(facts: Iterable[Any], policy: Mapping[str, Any]) -> dict:
    """Apply a ``lstest.coverage_policy.v1`` document to observed facts."""
    if not isinstance(policy, Mapping) or not policy:
        return _empty_policy_result(policy if isinstance(policy, Mapping) else None)
    schema = _as_text(policy.get("schema")).strip()
    if schema and schema != POLICY_SCHEMA:
        result = _empty_policy_result(policy)
        result["status"] = STATUS_BLOCKED
        result["reason"] = "unsupported_schema"
        result["notes"] = [f"unsupported coverage policy schema: {schema}"]
        return result

    items = coerce_facts(facts)
    roles_policy = policy.get("roles") if isinstance(policy.get("roles"), Mapping) else {}
    full_roles = _as_list(roles_policy.get("full"))
    minimum_roles = _as_list(roles_policy.get("minimum"))
    role_counts: dict[str, int] = {}
    for fact in items:
        role = _as_text(fact.role).strip()
        if role:
            role_counts[role] = role_counts.get(role, 0) + 1
    observed_roles = sorted(role_counts)

    missing_minimum = [role for role in minimum_roles if role not in role_counts]
    missing_non_minimum = [
        role for role in full_roles if role not in role_counts and role not in minimum_roles
    ]
    missing_roles = _unique(missing_minimum + missing_non_minimum)
    notes: list[str] = []
    if missing_minimum:
        status = _as_text(policy.get("missing_minimum") or STATUS_BLOCKED) or STATUS_BLOCKED
        notes.append("minimum roles missing: " + ", ".join(missing_minimum))
    elif missing_non_minimum:
        status = _as_text(policy.get("missing_non_minimum") or STATUS_DEGRADED) or STATUS_DEGRADED
        notes.append("non-minimum roles missing: " + ", ".join(missing_non_minimum))
    else:
        status = STATUS_PASS

    required_states = _as_list(policy.get("required_states"))
    states = state_coverage(items, required=required_states) if required_states else {
        "status": STATUS_SKIP,
        "covered": [],
        "missing": [],
        "counts": {},
        "notes": [],
    }
    if states["missing"]:
        notes.append("missing states: " + ", ".join(states["missing"]))
        if status == STATUS_PASS:
            status = STATUS_DEGRADED

    return {
        "schema": schema or POLICY_SCHEMA,
        "status": status,
        "reason": "" if status == STATUS_PASS else status.lower(),
        "roles": {"full": full_roles, "minimum": minimum_roles},
        "observed_roles": observed_roles,
        "missing_roles": missing_roles,
        "missing_minimum": missing_minimum,
        "missing_non_minimum": missing_non_minimum,
        "states": {
            "required": list(required_states),
            "covered": states["covered"],
            "missing": states["missing"],
        },
        "state_counts": states["counts"],
        "role_counts": dict(sorted(role_counts.items())),
        "notes": notes,
    }
