"""Case contract schema and validation for lstest.

The framework keeps the existing ``case_contract`` CSV column.  New metadata
lives under the reserved ``lstest`` key so old adapters and old result files
remain readable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


CASE_CONTRACT_VERSION = 1
SUPPORTED_CASE_CONTRACT_VERSIONS = frozenset({1})
BUILTIN_VERDICT_LAYERS = frozenset({"functional", "timing"})
VALID_OWNERS = frozenset(
    {"firmware", "device", "audio", "cloud", "config", "test-design", "infrastructure", "unknown"}
)
VALID_MISSING_POLICIES = frozenset({"empty", "ambiguous", "fail"})


class CaseContractError(ValueError):
    """Raised when a case contract is structurally invalid."""

    def __init__(self, code: str, message: str, field_name: str = "") -> None:
        self.code = str(code)
        self.field_name = str(field_name)
        super().__init__(f"{self.code}: {message}" if self.field_name else f"{self.code}: {message}")

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "field": self.field_name, "message": str(self)}


@dataclass(frozen=True)
class CaseContractIssue:
    code: str
    message: str
    field_name: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "field": self.field_name, "message": self.message}


@dataclass(frozen=True)
class VerdictLayerSpec:
    layer_id: str
    required: bool = True
    owner: str = "unknown"


@dataclass(frozen=True)
class TimingSpec:
    metric_id: str
    start_fact: str
    end_fact: str
    tolerance_ms: int | None = None
    missing_policy: str = "ambiguous"


@dataclass(frozen=True)
class NormalizedCaseContract:
    timeline: Mapping[str, Any] = field(default_factory=dict)
    requirement_ids: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    requires_capabilities: tuple[str, ...] = ()
    verdict_layers: tuple[VerdictLayerSpec, ...] = (
        VerdictLayerSpec("functional", True, "unknown"),
    )
    advisory_facts: tuple[str, ...] = ()
    forbidden_facts: tuple[str, ...] = ()
    timing: TimingSpec | None = None
    corpus_id: str | None = None
    version: int = CASE_CONTRACT_VERSION


def _string_list(
    value: Any,
    field_name: str,
    issues: list[CaseContractIssue],
    *,
    unique: bool = True,
) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple, set)):
        issues.append(CaseContractIssue("CASE_CONTRACT_INVALID", "must be a list of strings", field_name))
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item).strip()
        if not text:
            issues.append(CaseContractIssue("CASE_CONTRACT_INVALID", "must not contain empty strings", field_name))
            continue
        if unique and text in seen:
            continue
        seen.add(text)
        result.append(text)
    return tuple(result)


def _split_payload(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if "timeline" in payload or "lstest" in payload:
        timeline = payload.get("timeline", {})
        lstest = payload.get("lstest", {})
    else:
        timeline = payload
        lstest = {}
    if not isinstance(timeline, Mapping):
        raise CaseContractError("CASE_CONTRACT_INVALID", "timeline must be an object", "timeline")
    if not isinstance(lstest, Mapping):
        raise CaseContractError("CASE_CONTRACT_INVALID", "lstest must be an object", "lstest")
    return timeline, lstest


def _parse_layers(value: Any, issues: list[CaseContractIssue]) -> tuple[VerdictLayerSpec, ...]:
    if value is None:
        return (VerdictLayerSpec("functional", True, "unknown"),)
    if not isinstance(value, (list, tuple)) or not value:
        issues.append(CaseContractIssue("CASE_VERDICT_LAYER_INVALID", "verdict_layers must be a non-empty list", "lstest.verdict_layers"))
        return (VerdictLayerSpec("functional", True, "unknown"),)
    layers: list[VerdictLayerSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        location = f"lstest.verdict_layers[{index}]"
        if not isinstance(item, Mapping):
            issues.append(CaseContractIssue("CASE_VERDICT_LAYER_INVALID", "layer must be an object", location))
            continue
        layer_id = str(item.get("id") or item.get("layer_id") or "").strip()
        if layer_id not in BUILTIN_VERDICT_LAYERS:
            issues.append(CaseContractIssue("CASE_VERDICT_LAYER_INVALID", f"unsupported layer id: {layer_id}", f"{location}.id"))
            continue
        if layer_id in seen:
            issues.append(CaseContractIssue("CASE_VERDICT_LAYER_INVALID", f"duplicate layer id: {layer_id}", f"{location}.id"))
            continue
        seen.add(layer_id)
        required = item.get("required", True)
        if not isinstance(required, bool):
            issues.append(CaseContractIssue("CASE_VERDICT_LAYER_INVALID", "required must be boolean", f"{location}.required"))
            required = True
        owner = str(item.get("owner") or "unknown").strip()
        if owner not in VALID_OWNERS:
            issues.append(CaseContractIssue("CASE_VERDICT_LAYER_INVALID", f"unsupported owner: {owner}", f"{location}.owner"))
            owner = "unknown"
        layers.append(VerdictLayerSpec(layer_id, required, owner))
    if not any(layer.required for layer in layers):
        issues.append(CaseContractIssue("CASE_VERDICT_LAYER_INVALID", "at least one verdict layer must be required", "lstest.verdict_layers"))
    if not layers:
        return (VerdictLayerSpec("functional", True, "unknown"),)
    return tuple(layers)


def _parse_timing(
    value: Any,
    issues: list[CaseContractIssue],
    known_fact_keys: set[str] | None,
) -> TimingSpec | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        issues.append(CaseContractIssue("CASE_TIMING_INVALID", "timing must be an object", "lstest.timing"))
        return None
    metric_id = str(value.get("id") or value.get("metric_id") or "").strip()
    start_fact = str(value.get("start_fact") or "").strip().upper()
    end_fact = str(value.get("end_fact") or "").strip().upper()
    if not metric_id:
        issues.append(CaseContractIssue("CASE_TIMING_INVALID", "metric id is required", "lstest.timing.id"))
    if not start_fact or not end_fact:
        issues.append(CaseContractIssue("CASE_TIMING_INVALID", "start_fact and end_fact are required", "lstest.timing"))
    if start_fact and start_fact == end_fact:
        issues.append(CaseContractIssue("CASE_TIMING_INVALID", "start_fact and end_fact must differ", "lstest.timing"))
    tolerance = value.get("tolerance_ms")
    if tolerance is not None:
        if isinstance(tolerance, bool) or not isinstance(tolerance, int) or tolerance < 0:
            issues.append(CaseContractIssue("CASE_TIMING_INVALID", "tolerance_ms must be a non-negative integer", "lstest.timing.tolerance_ms"))
            tolerance = None
    missing_policy = str(value.get("missing_policy") or "ambiguous").strip()
    if missing_policy not in VALID_MISSING_POLICIES:
        issues.append(CaseContractIssue("CASE_TIMING_INVALID", f"unsupported missing_policy: {missing_policy}", "lstest.timing.missing_policy"))
        missing_policy = "ambiguous"
    if known_fact_keys:
        for name, fact in (("start_fact", start_fact), ("end_fact", end_fact)):
            if fact and fact not in known_fact_keys:
                issues.append(CaseContractIssue("CASE_TIMING_FACT_UNKNOWN", f"unknown timing fact: {fact}", f"lstest.timing.{name}"))
    if not metric_id or not start_fact or not end_fact:
        return None
    return TimingSpec(metric_id, start_fact, end_fact, tolerance, missing_policy)


def collect_case_contract_issues(
    payload: Mapping[str, Any],
    *,
    case_id: str | None = None,
    known_fact_keys: Iterable[str] | None = None,
    known_case_ids: Iterable[str] | None = None,
    known_requirement_ids: Iterable[str] | None = None,
    capability_ids: Iterable[str] | None = None,
    corpus_ids: Iterable[str] | None = None,
) -> list[CaseContractIssue]:
    """Return every structural/reference issue found in one case contract."""
    if not isinstance(payload, Mapping):
        return [CaseContractIssue("CASE_CONTRACT_INVALID", "case contract must be an object", "case_contract")]
    try:
        timeline, lstest = _split_payload(payload)
    except CaseContractError as error:
        return [CaseContractIssue(error.code, str(error), error.field_name)]
    issues: list[CaseContractIssue] = []

    version = lstest.get("contract_version", CASE_CONTRACT_VERSION)
    if isinstance(version, bool) or not isinstance(version, int):
        issues.append(CaseContractIssue("CASE_CONTRACT_INVALID", "contract_version must be an integer", "lstest.contract_version"))
        version = CASE_CONTRACT_VERSION
    elif version not in SUPPORTED_CASE_CONTRACT_VERSIONS:
        issues.append(CaseContractIssue("CASE_CONTRACT_VERSION_UNSUPPORTED", f"unsupported contract_version: {version}", "lstest.contract_version"))

    allowed = {
        "contract_version", "requirement_ids", "depends_on", "requires_capabilities",
        "verdict_layers", "evidence", "timing", "corpus_id",
    }
    unknown = sorted(set(lstest) - allowed)
    for name in unknown:
        issues.append(CaseContractIssue("CASE_CONTRACT_UNKNOWN_FIELD", f"unknown field: {name}", f"lstest.{name}"))

    required_facts = timeline.get("required_facts", ())
    if required_facts is not None and not isinstance(required_facts, (list, tuple, set)):
        issues.append(CaseContractIssue("CASE_CONTRACT_INVALID", "required_facts must be a list", "timeline.required_facts"))
    known = {str(item).upper() for item in known_fact_keys} if known_fact_keys is not None else None
    required_keys = {str(item).upper() for item in required_facts} if isinstance(required_facts, (list, tuple, set)) else set()
    if known:
        for fact in sorted(required_keys - known):
            issues.append(CaseContractIssue("CASE_REQUIRED_FACT_UNKNOWN", f"unknown required fact: {fact}", "timeline.required_facts"))

    requirement_ids = _string_list(lstest.get("requirement_ids"), "lstest.requirement_ids", issues)
    depends_on = _string_list(lstest.get("depends_on"), "lstest.depends_on", issues)
    capabilities = _string_list(lstest.get("requires_capabilities"), "lstest.requires_capabilities", issues)
    if case_id and set(depends_on) & {str(case_id)}:
        issues.append(CaseContractIssue("CASE_DEPENDENCY_INVALID", "case cannot depend on itself", "lstest.depends_on"))

    evidence = lstest.get("evidence", {})
    if evidence is None:
        evidence = {}
    if not isinstance(evidence, Mapping):
        issues.append(CaseContractIssue("CASE_EVIDENCE_INVALID", "evidence must be an object", "lstest.evidence"))
        evidence = {}
    advisory = _string_list(evidence.get("advisory"), "lstest.evidence.advisory", issues)
    forbidden = _string_list(evidence.get("forbidden"), "lstest.evidence.forbidden", issues)
    if set(advisory) & set(forbidden):
        issues.append(CaseContractIssue("CASE_EVIDENCE_CONFLICT", "a fact cannot be both advisory and forbidden", "lstest.evidence"))
    if set(required_keys) & set(forbidden):
        issues.append(CaseContractIssue("CASE_EVIDENCE_CONFLICT", "a fact cannot be both required and forbidden", "lstest.evidence.forbidden"))
    if known:
        for fact in sorted({*advisory, *forbidden} - known):
            issues.append(CaseContractIssue("CASE_EVIDENCE_FACT_UNKNOWN", f"unknown evidence fact: {fact}", "lstest.evidence"))

    verdict_layers = _parse_layers(lstest.get("verdict_layers"), issues)
    timing = _parse_timing(lstest.get("timing"), issues, known)

    corpus_id_raw = lstest.get("corpus_id")
    corpus_id = str(corpus_id_raw).strip() if corpus_id_raw not in (None, "") else None

    if known_case_ids is not None:
        known_cases = {str(item) for item in known_case_ids}
        for dependency in depends_on:
            if dependency not in known_cases:
                issues.append(CaseContractIssue("CASE_DEPENDENCY_UNKNOWN", f"unknown case dependency: {dependency}", "lstest.depends_on"))
    if known_requirement_ids is not None:
        known_requirements = {str(item) for item in known_requirement_ids}
        for requirement_id in requirement_ids:
            if requirement_id not in known_requirements:
                issues.append(CaseContractIssue("CASE_REQUIREMENT_UNKNOWN", f"unknown requirement id: {requirement_id}", "lstest.requirement_ids"))
    if capability_ids is not None:
        known_capabilities = {str(item) for item in capability_ids}
        for capability in capabilities:
            if capability not in known_capabilities:
                issues.append(CaseContractIssue("CASE_CAPABILITY_UNKNOWN", f"unknown capability: {capability}", "lstest.requires_capabilities"))
    if corpus_ids is not None and corpus_id and corpus_id not in {str(item) for item in corpus_ids}:
        issues.append(CaseContractIssue("CASE_CORPUS_UNKNOWN", f"unknown corpus id: {corpus_id}", "lstest.corpus_id"))

    # Keep the parsed values alive for callers that only want validation.
    _ = (requirement_ids, verdict_layers, timing, version)
    return issues


def assert_case_contract(
    payload: Mapping[str, Any],
    *,
    case_id: str | None = None,
    known_fact_keys: Iterable[str] | None = None,
    known_case_ids: Iterable[str] | None = None,
    known_requirement_ids: Iterable[str] | None = None,
    capability_ids: Iterable[str] | None = None,
    corpus_ids: Iterable[str] | None = None,
) -> None:
    issues = collect_case_contract_issues(
        payload,
        case_id=case_id,
        known_fact_keys=known_fact_keys,
        known_case_ids=known_case_ids,
        known_requirement_ids=known_requirement_ids,
        capability_ids=capability_ids,
        corpus_ids=corpus_ids,
    )
    if issues:
        first = issues[0]
        raise CaseContractError(first.code, first.message, first.field_name)


def normalize_case_contract(
    payload: Mapping[str, Any],
    *,
    case_id: str | None = None,
    known_fact_keys: Iterable[str] | None = None,
) -> NormalizedCaseContract:
    """Return a validated contract; legacy timeline-only payloads stay valid."""
    assert_case_contract(payload, case_id=case_id, known_fact_keys=known_fact_keys)
    timeline, lstest = _split_payload(payload)
    issues: list[CaseContractIssue] = []
    version = int(lstest.get("contract_version", CASE_CONTRACT_VERSION))
    return NormalizedCaseContract(
        timeline=dict(timeline),
        requirement_ids=_string_list(lstest.get("requirement_ids"), "lstest.requirement_ids", issues),
        depends_on=_string_list(lstest.get("depends_on"), "lstest.depends_on", issues),
        requires_capabilities=_string_list(lstest.get("requires_capabilities"), "lstest.requires_capabilities", issues),
        verdict_layers=_parse_layers(lstest.get("verdict_layers"), issues),
        advisory_facts=_string_list((lstest.get("evidence") or {}).get("advisory"), "lstest.evidence.advisory", issues),
        forbidden_facts=_string_list((lstest.get("evidence") or {}).get("forbidden"), "lstest.evidence.forbidden", issues),
        timing=_parse_timing(lstest.get("timing"), issues, {str(item).upper() for item in known_fact_keys} if known_fact_keys is not None else None),
        corpus_id=(str(lstest.get("corpus_id")).strip() if lstest.get("corpus_id") not in (None, "") else None),
        version=version,
    )

