"""Read-only validation for an lstest result directory."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from .case_contract import CaseContractError, assert_case_contract
    from .verdict import validate_verdict_payload
except ImportError:  # direct execution fallback
    from case_contract import CaseContractError, assert_case_contract  # type: ignore
    from verdict import validate_verdict_payload  # type: ignore


RESULT_STATUSES = frozenset(
    {"PASS", "FAIL", "BLOCKED", "PARTIAL", "AMBIGUOUS", "SKIPPED", "STOPPED", "EXPECTED", "ABORTED"}
)
ALLOWED_TOP_LEVEL = frozenset({"serial_logs", "tool.log", "results.csv", "cases.csv", "STOP"})
CASE_REQUIRED_COLUMNS = frozenset({"case_id", "scenario", "input_text", "audio_file", "case_contract"})
RESULT_REQUIRED_COLUMNS = frozenset({"case_id", "final_status", "facts_json", "evidence", "primary_reason"})
RESULT_LINE_RE = re.compile(r"\[RESULT\].*?(?:本轮|result)\s*=\s*([A-Z_]+)", re.IGNORECASE)


def _read_header(path: Path) -> tuple[str, ...]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return tuple(next(csv.reader(handle), ()))


@dataclass(frozen=True)
class ResultIssue:
    code: str
    message: str
    field_name: str = ""
    case_id: str = ""

    def to_dict(self) -> dict[str, str]:
        payload = {"code": self.code, "message": self.message}
        if self.field_name:
            payload["field"] = self.field_name
        if self.case_id:
            payload["case_id"] = self.case_id
        return payload


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _parse_json(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (Mapping, list)):
        return value
    return json.loads(str(value))


def _validate_evidence(run_dir: Path, evidence: str, issues: list[ResultIssue], case_id: str) -> None:
    for item in str(evidence or "").split(";"):
        ref = item.strip()
        if not ref:
            continue
        path_text, separator, cursor_text = ref.partition("#")
        path = Path(path_text)
        if not path.is_absolute():
            path = run_dir / path
        try:
            path.resolve().relative_to(run_dir.resolve())
        except ValueError:
            issues.append(ResultIssue("RESULT_EVIDENCE_CURSOR_INVALID", f"evidence path escapes run directory: {ref}", "evidence", case_id))
            continue
        if not path.is_file():
            issues.append(ResultIssue("RESULT_EVIDENCE_MISSING", f"evidence file not found: {ref}", "evidence", case_id))
            continue
        if separator:
            try:
                cursor = int(cursor_text)
            except ValueError:
                issues.append(ResultIssue("RESULT_EVIDENCE_CURSOR_INVALID", f"cursor is not an integer: {ref}", "evidence", case_id))
                continue
            if cursor < 1:
                issues.append(ResultIssue("RESULT_EVIDENCE_CURSOR_INVALID", f"cursor must be >= 1: {ref}", "evidence", case_id))
                continue
            try:
                line_count = sum(1 for _ in path.open("r", encoding="utf-8", errors="replace"))
            except OSError:
                line_count = 0
            if cursor > line_count:
                issues.append(ResultIssue("RESULT_EVIDENCE_CURSOR_INVALID", f"cursor {cursor} exceeds {line_count} lines: {ref}", "evidence", case_id))


def validate_run_dir(
    run_dir: Path | str,
    *,
    requirement_ids: Iterable[str] | None = None,
    capability_ids: Iterable[str] | None = None,
    corpus_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Validate the four lstest artifacts without modifying the run directory."""
    root = Path(run_dir).resolve()
    issues: list[ResultIssue] = []
    if not root.is_dir():
        return {"status": "BLOCKED", "issues": [ResultIssue("RESULT_DIRECTORY_MISSING", str(root)).to_dict()]}

    for name in ("tool.log", "results.csv", "cases.csv"):
        if not (root / name).is_file():
            issues.append(ResultIssue("RESULT_ARTIFACT_MISSING", f"missing {name}", name))
    if not (root / "serial_logs").is_dir():
        issues.append(ResultIssue("RESULT_ARTIFACT_MISSING", "missing serial_logs/", "serial_logs"))
    for entry in root.iterdir():
        if entry.name not in ALLOWED_TOP_LEVEL:
            issues.append(ResultIssue("RESULT_ARTIFACT_EXTRA", f"unexpected artifact: {entry.name}", entry.name))

    if issues:
        return {"status": "BLOCKED", "issues": [issue.to_dict() for issue in issues], "run_dir": str(root)}

    try:
        case_header = _read_header(root / "cases.csv")
        result_header = _read_header(root / "results.csv")
    except OSError as error:
        issues.append(ResultIssue("RESULT_CSV_INVALID", f"CSV header: {error}", "csv"))
        case_header = ()
        result_header = ()
    for name in sorted(CASE_REQUIRED_COLUMNS - set(case_header)):
        issues.append(ResultIssue("RESULT_HEADER_MISSING", f"cases.csv missing column: {name}", "cases.csv"))
    for name in sorted(RESULT_REQUIRED_COLUMNS - set(result_header)):
        issues.append(ResultIssue("RESULT_HEADER_MISSING", f"results.csv missing column: {name}", "results.csv"))

    try:
        cases = _read_csv(root / "cases.csv")
    except Exception as error:
        issues.append(ResultIssue("RESULT_CSV_INVALID", f"cases.csv: {error}", "cases.csv"))
        cases = []
    try:
        results = _read_csv(root / "results.csv")
    except Exception as error:
        issues.append(ResultIssue("RESULT_CSV_INVALID", f"results.csv: {error}", "results.csv"))
        results = []

    case_ids: list[str] = []
    case_by_id: dict[str, dict[str, str]] = {}
    for row in cases:
        case_id = str(row.get("case_id") or "").strip()
        if not case_id:
            issues.append(ResultIssue("RESULT_CASE_ID_MISSING", "cases.csv row has no case_id", "cases.csv"))
            continue
        if case_id in case_by_id:
            issues.append(ResultIssue("RESULT_CASE_DUPLICATE", f"duplicate case_id in cases.csv: {case_id}", "cases.csv", case_id))
            continue
        case_ids.append(case_id)
        case_by_id[case_id] = row

    result_ids: set[str] = set()
    result_status_counts: dict[str, int] = {}
    for row in results:
        case_id = str(row.get("case_id") or "").strip()
        if not case_id:
            issues.append(ResultIssue("RESULT_CASE_ID_MISSING", "results.csv row has no case_id", "results.csv"))
            continue
        if case_id in result_ids:
            issues.append(ResultIssue("RESULT_ROW_COUNT_MISMATCH", f"duplicate result row for case: {case_id}", "results.csv", case_id))
            continue
        result_ids.add(case_id)
        if case_id not in case_by_id:
            issues.append(ResultIssue("RESULT_CASE_UNKNOWN", f"result case not present in cases.csv: {case_id}", "results.csv", case_id))
        status = str(row.get("final_status") or "").strip().upper()
        if status not in RESULT_STATUSES:
            issues.append(ResultIssue("RESULT_STATUS_INVALID", f"unsupported final_status: {status}", "results.csv", case_id))
        result_status_counts[status] = result_status_counts.get(status, 0) + 1

        for field_name in ("raw_fact_counts", "logical_fact_counts", "association", "facts_json"):
            try:
                _parse_json(row.get(field_name))
            except Exception as error:
                issues.append(ResultIssue("RESULT_JSON_INVALID", f"{field_name}: {error}", "results.csv", case_id))

        facts = None
        try:
            facts = _parse_json(row.get("facts_json"))
        except Exception:
            pass
        if isinstance(facts, Mapping):
            lstest_meta = facts.get("_lstest")
            if isinstance(lstest_meta, Mapping):
                version = lstest_meta.get("schema_version", 1)
                if version != 1:
                    issues.append(ResultIssue("RESULT_SCHEMA_UNSUPPORTED", f"unsupported _lstest.schema_version: {version}", "results.csv", case_id))
                verdict_payload = lstest_meta.get("verdict")
                if verdict_payload is not None:
                    for issue in validate_verdict_payload(verdict_payload):
                        issues.append(ResultIssue(issue.code, issue.message, issue.field_name, case_id))
                    verdict_status = str((verdict_payload or {}).get("status") or "").upper()
                    if verdict_status and status and verdict_status != status:
                        issues.append(ResultIssue("RESULT_VERDICT_CONFLICT", f"final_status={status} but verdict.status={verdict_status}", "results.csv", case_id))

        _validate_evidence(root, str(row.get("evidence") or ""), issues, case_id)

        contract_text = case_by_id.get(case_id, {}).get("case_contract")
        if contract_text:
            try:
                contract = _parse_json(contract_text)
                assert_case_contract(
                    contract,
                    case_id=case_id,
                    known_case_ids=case_ids,
                    known_requirement_ids=requirement_ids,
                    capability_ids=capability_ids,
                    corpus_ids=corpus_ids,
                )
            except (CaseContractError, ValueError) as error:
                issues.append(ResultIssue("RESULT_CASE_CONTRACT_INVALID", str(error), "case_contract", case_id))

    missing_results = [case_id for case_id in case_ids if case_id not in result_ids]
    for case_id in missing_results:
        issues.append(ResultIssue(
            "RESULT_ROW_MISSING",
            f"cases.csv case has no results.csv row: {case_id}",
            "results.csv",
            case_id,
        ))

    try:
        tool_text = (root / "tool.log").read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        issues.append(ResultIssue("RESULT_TOOL_LOG_INVALID", str(error), "tool.log"))
        tool_text = ""
    tool_result_statuses = [match.group(1).upper() for match in RESULT_LINE_RE.finditer(tool_text)]
    if len(tool_result_statuses) != len(results):
        issues.append(ResultIssue(
            "RESULT_COUNT_MISMATCH",
            f"tool.log [RESULT] count={len(tool_result_statuses)} but results.csv rows={len(results)}",
            "tool.log",
        ))
    tool_status_counts: dict[str, int] = {}
    for status in tool_result_statuses:
        tool_status_counts[status] = tool_status_counts.get(status, 0) + 1
    for status, count in tool_status_counts.items():
        if result_status_counts.get(status, 0) != count:
            issues.append(ResultIssue(
                "RESULT_COUNT_MISMATCH",
                f"status {status}: tool.log={count} results.csv={result_status_counts.get(status, 0)}",
                "tool.log",
            ))

    stop_requested = (root / "STOP").exists()
    if stop_requested and not any(status in {"STOPPED", "ABORTED"} for status in result_status_counts):
        issues.append(ResultIssue(
            "RESULT_STOP_WITHOUT_STOPPED",
            "STOP exists but results.csv has no STOPPED/ABORTED case",
            "STOP",
        ))

    status = "PASS" if not issues else "FAIL"
    return {
        "status": status,
        "run_dir": str(root),
        "issues": [issue.to_dict() for issue in issues],
        "cases": len(cases),
        "results": len(results),
        "missing_results": missing_results,
        "result_status_counts": dict(sorted(result_status_counts.items())),
        "tool_result_status_counts": dict(sorted(tool_status_counts.items())),
        "stop_requested": stop_requested,
    }
