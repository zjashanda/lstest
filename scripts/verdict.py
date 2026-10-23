"""Verdict algebra for lstest.

A case can carry several verdict layers.  The final status is a deterministic
aggregation, not a free-form project value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping


class VerdictStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    PARTIAL = "PARTIAL"
    AMBIGUOUS = "AMBIGUOUS"
    SKIPPED = "SKIPPED"
    ABORTED = "ABORTED"
    STOPPED = "STOPPED"


VALID_VERDICT_STATUSES = frozenset(item.value for item in VerdictStatus)
VALID_LAYER_IDS = frozenset({"functional", "timing"})
VALID_OWNERS = frozenset(
    {"firmware", "device", "audio", "cloud", "config", "test-design", "infrastructure", "unknown"}
)


@dataclass(frozen=True)
class VerdictIssue:
    code: str
    message: str
    field_name: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "field": self.field_name, "message": self.message}


@dataclass(frozen=True)
class VerdictLayerResult:
    layer_id: str
    status: VerdictStatus
    required: bool = True
    owner: str = "unknown"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.layer_id,
            "status": self.status.value,
            "required": self.required,
            "owner": self.owner,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Verdict:
    status: VerdictStatus
    layers: tuple[VerdictLayerResult, ...] = ()
    anomaly_codes: tuple[str, ...] = ()
    primary_reason: str = ""
    blocked_by: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status.value,
            "layers": [layer.to_dict() for layer in self.layers],
            "anomaly_codes": list(self.anomaly_codes),
            "primary_reason": self.primary_reason,
        }
        if self.blocked_by:
            payload["blocked_by"] = dict(self.blocked_by)
        return payload


def _normalize_status(value: Any, *, field_name: str = "status") -> VerdictStatus:
    text = str(value or "").strip().upper()
    if text == "EXPECTED":
        text = VerdictStatus.PASS.value
    try:
        return VerdictStatus(text)
    except ValueError as error:
        raise ValueError(f"{field_name} must be one of {sorted(VALID_VERDICT_STATUSES)}; got {value!r}") from error


def verdict_from_status(
    status: Any,
    reason: str = "",
    *,
    owner: str = "unknown",
    anomaly_codes: Iterable[str] = (),
) -> Verdict:
    """Build a one-layer verdict for legacy callers."""
    normalized = _normalize_status(status)
    layer_status = normalized
    if normalized is VerdictStatus.PARTIAL:
        layer_status = VerdictStatus.PARTIAL
    owner_value = owner if owner in VALID_OWNERS else "unknown"
    layer = VerdictLayerResult("functional", layer_status, True, owner_value, reason)
    return Verdict(normalized, (layer,), tuple(anomaly_codes), reason)


def aggregate_verdict(
    layers: Iterable[VerdictLayerResult],
    *,
    anomaly_codes: Iterable[str] = (),
    blocked_by: Mapping[str, Any] | None = None,
    primary_reason: str = "",
) -> Verdict:
    """Aggregate required/advisory layers into one deterministic status."""
    values = list(layers)
    if not values:
        raise ValueError("at least one verdict layer is required")
    required = [layer for layer in values if layer.required]
    advisory = [layer for layer in values if not layer.required]
    if not required:
        raise ValueError("at least one verdict layer must be required")

    codes = list(dict.fromkeys(str(code) for code in anomaly_codes if str(code)))
    reason = primary_reason

    if any(layer.status is VerdictStatus.FAIL for layer in required):
        failed = [layer for layer in required if layer.status is VerdictStatus.FAIL]
        reason = reason or failed[0].reason or "required verdict layer failed"
        return Verdict(VerdictStatus.FAIL, tuple(values), tuple(codes), reason, blocked_by)
    if blocked_by:
        code = str(blocked_by.get("code") or "VERDICT_BLOCKED")
        return Verdict(VerdictStatus.BLOCKED, tuple(values), tuple(dict.fromkeys([*codes, code])), reason or str(blocked_by.get("reason") or "blocked"), blocked_by)
    if any(layer.status is VerdictStatus.BLOCKED for layer in required):
        blocked = [layer for layer in required if layer.status is VerdictStatus.BLOCKED]
        return Verdict(VerdictStatus.BLOCKED, tuple(values), tuple(dict.fromkeys([*codes, "VERDICT_BLOCKED_LAYER"])), reason or blocked[0].reason)
    if any(layer.status is VerdictStatus.AMBIGUOUS for layer in required):
        ambiguous = [layer for layer in required if layer.status is VerdictStatus.AMBIGUOUS]
        return Verdict(VerdictStatus.AMBIGUOUS, tuple(values), tuple(dict.fromkeys([*codes, "VERDICT_TIMING_AMBIGUOUS"])), reason or ambiguous[0].reason)
    if any(layer.status in {VerdictStatus.FAIL, VerdictStatus.BLOCKED, VerdictStatus.AMBIGUOUS, VerdictStatus.PARTIAL} for layer in advisory):
        return Verdict(VerdictStatus.PARTIAL, tuple(values), tuple(dict.fromkeys([*codes, "VERDICT_ADVISORY_FAILURE"])), reason or "advisory verdict layer did not pass")
    if all(layer.status is VerdictStatus.PASS for layer in required):
        return Verdict(VerdictStatus.PASS, tuple(values), tuple(codes), reason)
    return Verdict(VerdictStatus.SKIPPED, tuple(values), tuple(codes), reason or "case was not executed")


def validate_verdict_payload(payload: Mapping[str, Any]) -> list[VerdictIssue]:
    """Validate a serialized ``facts_json._lstest.verdict`` object."""
    if not isinstance(payload, Mapping):
        return [VerdictIssue("VERDICT_INVALID", "verdict must be an object", "verdict")]
    issues: list[VerdictIssue] = []
    status_text = str(payload.get("status") or "").strip().upper()
    if status_text not in VALID_VERDICT_STATUSES:
        issues.append(VerdictIssue("VERDICT_STATUS_INVALID", f"unsupported status: {status_text}", "verdict.status"))
        status = None
    else:
        status = VerdictStatus(status_text)

    layers_raw = payload.get("layers")
    layers: list[VerdictLayerResult] = []
    if not isinstance(layers_raw, (list, tuple)) or not layers_raw:
        issues.append(VerdictIssue("VERDICT_LAYER_INVALID", "layers must be a non-empty list", "verdict.layers"))
    else:
        seen: set[str] = set()
        for index, item in enumerate(layers_raw):
            location = f"verdict.layers[{index}]"
            if not isinstance(item, Mapping):
                issues.append(VerdictIssue("VERDICT_LAYER_INVALID", "layer must be an object", location))
                continue
            layer_id = str(item.get("id") or item.get("layer_id") or "").strip()
            if layer_id not in VALID_LAYER_IDS:
                issues.append(VerdictIssue("VERDICT_LAYER_INVALID", f"unsupported layer id: {layer_id}", f"{location}.id"))
                continue
            if layer_id in seen:
                issues.append(VerdictIssue("VERDICT_LAYER_INVALID", f"duplicate layer id: {layer_id}", f"{location}.id"))
                continue
            seen.add(layer_id)
            required = item.get("required", True)
            if not isinstance(required, bool):
                issues.append(VerdictIssue("VERDICT_LAYER_INVALID", "required must be boolean", f"{location}.required"))
                required = True
            owner = str(item.get("owner") or "unknown").strip()
            if owner not in VALID_OWNERS:
                issues.append(VerdictIssue("VERDICT_LAYER_INVALID", f"unsupported owner: {owner}", f"{location}.owner"))
                owner = "unknown"
            try:
                layer_status = _normalize_status(item.get("status"), field_name=f"{location}.status")
            except ValueError as error:
                issues.append(VerdictIssue("VERDICT_LAYER_INVALID", str(error), f"{location}.status"))
                continue
            layers.append(VerdictLayerResult(layer_id, layer_status, required, owner, str(item.get("reason") or "")))

    if layers and not any(layer.required for layer in layers):
        issues.append(VerdictIssue("VERDICT_LAYER_INVALID", "at least one layer must be required", "verdict.layers"))

    if status is not None and layers:
        required = [layer for layer in layers if layer.required]
        advisory = [layer for layer in layers if not layer.required]
        if status is VerdictStatus.PASS and any(layer.status is not VerdictStatus.PASS for layer in required):
            issues.append(VerdictIssue("VERDICT_STATUS_CONFLICT", "PASS requires every required layer to be PASS", "verdict.status"))
        if status is VerdictStatus.FAIL and not any(layer.status is VerdictStatus.FAIL for layer in required):
            issues.append(VerdictIssue("VERDICT_STATUS_CONFLICT", "FAIL requires a required layer to be FAIL", "verdict.status"))
        if status is VerdictStatus.PARTIAL and (any(layer.status is not VerdictStatus.PASS for layer in required) or not any(layer.status is not VerdictStatus.PASS for layer in advisory)):
            issues.append(VerdictIssue("VERDICT_STATUS_CONFLICT", "PARTIAL requires required PASS and advisory non-PASS", "verdict.status"))
        if status is VerdictStatus.BLOCKED and not payload.get("blocked_by") and not any(layer.status is VerdictStatus.BLOCKED for layer in required):
            issues.append(VerdictIssue("VERDICT_STATUS_CONFLICT", "BLOCKED requires blocked_by or a required BLOCKED layer", "verdict.status"))
    return issues
