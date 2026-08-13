"""Core task, connection, evidence, and scenario contracts for lstest."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

try:
    from .observations import RawTag, ToolJudgement, raw_tags_from_mappings, render_observation
except ImportError:  # direct execution fallback
    from observations import RawTag, ToolJudgement, raw_tags_from_mappings, render_observation

BEIJING = timezone(timedelta(hours=8))
STICKY_FATAL_MARKERS = {
    "PANIC", "CRASH", "ASSERT", "WATCHDOG_RESET", "UNEXPECTED_REBOOT",
    "DATA_CORRUPTION", "SERIAL_LOSS", "TOOL_EXCEPTION",
}
NORMAL_CASE_STATUSES = {"PASS", "EXPECTED"}


def now_dt() -> datetime:
    return datetime.now(BEIJING)


def now_iso() -> str:
    return now_dt().isoformat(timespec="milliseconds")


def now_human() -> str:
    return now_dt().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class PortSpec:
    port: str
    baudrate: int
    role: str | None = None
    capabilities: tuple[str, ...] = ()
    required_for: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["capabilities"] = list(self.capabilities)
        result["required_for"] = list(self.required_for)
        return result


@dataclass(frozen=True)
class ConnectionSpec:
    ports: tuple[PortSpec, ...] = ()
    playback_device_key: str | None = None
    capture_device_key: str | None = None
    profile_id: str | None = None
    result_root: Path = Path("result")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ports": [item.to_dict() for item in self.ports],
            "playback_device_key": self.playback_device_key,
            "playback_target_mode": "specified_device_key" if self.playback_device_key else "system_default_render",
            "capture_device_key": self.capture_device_key,
            "profile_id": self.profile_id,
            "result_root": str(self.result_root),
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ConnectionSpec":
        raw_ports = payload.get("ports") or []
        ports: list[PortSpec] = []
        for raw in raw_ports:
            if isinstance(raw, str):
                parts = raw.split(":")
                raw = {"port": parts[0], "role": parts[1] if len(parts) > 1 else None}
            if not isinstance(raw, Mapping):
                raise ValueError("each port must be a string or object")
            port = str(raw.get("port", "")).strip()
            if not port:
                raise ValueError("port cannot be empty")
            baudrate = int(raw.get("baudrate", payload.get("baudrate", 0)))
            if baudrate <= 0:
                raise ValueError(f"invalid baudrate for {port}")
            ports.append(PortSpec(
                port=port,
                baudrate=baudrate,
                role=str(raw["role"]).strip() if raw.get("role") else None,
                capabilities=tuple(str(value) for value in raw.get("capabilities", ())),
                required_for=tuple(str(value) for value in raw.get("required_for", ())),
            ))
        return cls(
            ports=tuple(ports),
            playback_device_key=payload.get("playback_device_key"),
            capture_device_key=payload.get("capture_device_key"),
            profile_id=payload.get("profile_id"),
            result_root=Path(payload.get("result_root", "result")),
        )


@dataclass
class CaseResult:
    case_id: str
    raw_status: str
    reviewed_status: str | None = None
    reason: str = ""
    evidence: list[str] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    text: str = ""
    audio_path: str | None = None
    domain: str | None = None
    intent: str | None = None
    action: str | None = None
    destructive: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class ObservedFacts:
    case_id: str
    markers: list[dict[str, Any]] = field(default_factory=list)
    wake: dict[str, Any] = field(default_factory=dict)
    asr: dict[str, Any] = field(default_factory=dict)
    business: dict[str, Any] = field(default_factory=dict)
    player: dict[str, Any] = field(default_factory=dict)
    correlation: dict[str, Any] = field(default_factory=dict)
    timing: dict[str, Any] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)


class Scenario(Protocol):
    """Scenario contract implemented by project adapters."""

    def preflight(self, runtime: "DeviceRuntime") -> None: ...
    def next_case(self, state: Any) -> CaseSpec | None: ...
    def run_case(self, case: CaseSpec, runtime: "DeviceRuntime") -> ObservedFacts: ...
    def judge(self, observed: ObservedFacts) -> CaseResult: ...
    def cleanup(self, runtime: "DeviceRuntime") -> None: ...


class StopSupervisor:
    def __init__(self, stop_file: Path, *, disk_limit_bytes: int = 1024**3):
        self.stop_file = stop_file
        self.disk_limit_bytes = disk_limit_bytes
        self._event = threading.Event()
        self.reason = ""

    def request(self, reason: str = "requested") -> None:
        self.reason = reason
        self._event.set()

    @property
    def requested(self) -> bool:
        return self._event.is_set() or self.stop_file.is_file()

    def disk_ok(self, path: Path) -> bool:
        try:
            return path.stat().st_size >= 0 and shutil.disk_usage(path).free > self.disk_limit_bytes
        except OSError:
            return False

    def check(self, path: Path | None = None) -> str | None:
        """返回停止原因；磁盘低于停止线时阻止新的用例。"""
        if self.stop_file.is_file():
            return self.reason or "STOP_FILE"
        if path is not None and not self.disk_ok(path):
            self.request("DISK_STOP_LINE")
            return self.reason
        return self.reason if self.requested else None

    def wait(self, seconds: float, *, tick: float = 0.1, path: Path | None = None) -> str | None:
        """可中断、有上限的等待；永不向 time.sleep 传递负数。"""
        duration = max(0.0, float(seconds))
        deadline = time.monotonic() + duration
        while True:
            reason = self.check(path)
            if reason:
                return reason
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._event.wait(min(max(0.01, tick), remaining))

    def raise_if_requested(self, path: Path | None = None) -> None:
        reason = self.check(path)
        if reason:
            raise RuntimeError(reason)


class TaskArtifacts:
    """The four-artifact result ledger used by every new lstest task.

    ``serial_logs/`` is the continuous device-side fact source.  ``tool.log``
    is the single human-readable execution ledger.  ``results.csv`` contains
    one final row per logical case, and ``cases.csv`` freezes the input order
    before any device action.  No derived JSON, per-tool log, or summary file
    is created for new tasks.
    """

    TOOL_FIELDS = (
        "time", "level", "event", "task_id", "epoch", "round", "case_id",
        "phase", "source", "device", "port_role", "action_id", "broadcast_id",
        "correlation_id", "status", "reason", "attempt", "max_attempts",
        "elapsed_ms", "rule_id", "expected", "actual", "raw", "normalized",
        "handling", "evidence", "message", "details",
    )
    RESULT_FIELDS = (
        "round", "case_id", "started_at", "ended_at", "epoch", "scenario",
        "input_text", "audio_path", "audio_sha256", "broadcast_id", "wake_attempts",
        "wake_raw", "offline_attempts", "offline_keyword_raw", "offline_intent_raw",
        "online_request_id", "online_response_id", "online_raw_response",
        "player_status", "device_playback_status", "command_audio_duration_ms",
        "e2e_latency_ms", "processing_latency_ms", "recognition_latency_ms",
        "correlation_valid", "raw_exact_status", "semantic_status", "checks_summary",
        "raw_status", "reviewed_status", "final_status", "anomaly_codes", "reason",
        "evidence", "facts_json",
    )
    CASE_FIELDS = (
        "case_order", "case_id", "scenario", "input_text", "audio_path", "audio_sha256",
        "expected_wake_raw", "expected_offline_raw", "expected_online_raw",
        "accepted_raw_variants", "source_file", "source_sha256", "random_seed",
        "profile_version", "profile_sha256",
    )

    def __init__(self, result_root: Path, task_slug: str, detail_fields: Sequence[str] = ()):
        stamp = now_dt().strftime("%Y%m%d_%H%M%S")
        base = Path(result_root) / f"{stamp}_{task_slug}"
        candidate = base
        suffix = 1
        while candidate.exists():
            candidate = Path(f"{base}_{suffix:02d}")
            suffix += 1
        self.run_dir = candidate
        (self.run_dir / "serial_logs").mkdir(parents=True, exist_ok=False)
        self.task_id = self.run_dir.name
        self.tool_log_path = self.run_dir / "tool.log"
        self.results_path = self.run_dir / "results.csv"
        self.cases_path = self.run_dir / "cases.csv"
        self.stop = StopSupervisor(self.run_dir / "STOP")
        self._lock = threading.RLock()
        self._tool = self.tool_log_path.open("a", encoding="utf-8")
        result_fields = list(dict.fromkeys([*self.RESULT_FIELDS, *detail_fields]))
        self._csv = self.results_path.open("w", encoding="utf-8-sig", newline="")
        self._writer = csv.DictWriter(self._csv, fieldnames=result_fields, extrasaction="ignore")
        self._writer.writeheader()
        self._csv.flush()
        self._cases = self.cases_path.open("w", encoding="utf-8-sig", newline="")
        self._cases_writer = csv.DictWriter(self._cases, fieldnames=self.CASE_FIELDS, extrasaction="ignore")
        self._cases_writer.writeheader()
        self._cases.flush()
        self._cases_frozen = False
        self._frozen_case_count = 0
        self.counts: dict[str, int] = {}
        self.exception_counts: dict[str, int] = {}
        self.anomaly_counts: dict[str, int] = {}
        self.completed_cases = 0
        self.sticky: list[dict[str, Any]] = []
        self.capabilities: dict[str, dict[str, Any]] = {}
        self.checks: dict[str, list[dict[str, Any]]] = {}
        self.current_epoch = 0
        self._epoch_listeners: list[Any] = []
        self.health_policy: dict[str, Any] = {}
        self.health_streaks: dict[str, int] = {}
        self._closers: list[tuple[str, Any]] = []
        self._final_summary: dict[str, Any] | None = None
        self.closed = False
        self.emit(
            "TASK_STARTED",
            message="已创建四类结果产物：串口日志、工具日志、结果表和用例表。",
            task_slug=task_slug,
            result_directory=str(self.run_dir),
            result_artifacts=["serial_logs/", "tool.log", "results.csv", "cases.csv"],
        )

    @staticmethod
    def _text(value: Any) -> str:
        if value is None or value == "":
            return "-"
        if isinstance(value, str):
            return value.replace("\r\n", "\\n").replace("\n", "\\n")
        if isinstance(value, (Mapping, list, tuple, set)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return str(value)

    def _tool_block(self, event: str, level: str, message: str, fields: Mapping[str, Any]) -> str:
        values = dict(fields)
        port = values.pop("port", "")
        role = values.pop("role", "")
        port_role = values.pop("port_role", "") or "/".join(item for item in (str(port), str(role)) if item)
        evidence = values.pop("evidence", values.pop("evidence_refs", ""))
        raw = values.pop("raw", values.pop("raw_tags", values.pop("raw_line", "")))
        normalized = values.pop("normalized", "")
        expected = values.pop("expected", "")
        actual = values.pop("actual", "")
        known = {
            "time": now_human(),
            "level": level.upper(),
            "event": event,
            "task_id": self.task_id,
            "epoch": values.pop("epoch", self.current_epoch),
            "round": values.pop("round", values.pop("completed_cases", "")),
            "case_id": values.pop("case_id", ""),
            "phase": values.pop("phase", ""),
            "source": values.pop("source", ""),
            "device": values.pop("device", ""),
            "port_role": port_role,
            "action_id": values.pop("action_id", ""),
            "broadcast_id": values.pop("broadcast_id", ""),
            "correlation_id": values.pop("correlation_id", values.pop("query_id", values.pop("request_id", ""))),
            "status": values.pop("status", values.pop("tool_status", "")),
            "reason": values.pop("reason", values.pop("tool_reason", "")),
            "attempt": values.pop("attempt", ""),
            "max_attempts": values.pop("max_attempts", ""),
            "elapsed_ms": values.pop("elapsed_ms", values.pop("duration_ms", "")),
            "rule_id": values.pop("rule_id", ""),
            "expected": expected,
            "actual": actual,
            "raw": raw,
            "normalized": normalized,
            "handling": values.pop("handling", ""),
            "evidence": evidence,
            "message": message,
            "details": values,
        }
        return "\n".join(
            ["=" * 78, *[f"{name}: {self._text(known[name])}" for name in self.TOOL_FIELDS], ""]
        ) + "\n"

    def configure(self, payload: Mapping[str, Any]) -> None:
        """Record a non-sensitive resolved configuration in the sole tool log."""
        self.emit("TASK_CONFIG", message="已记录任务解析配置。", phase="preflight", raw=dict(payload))

    def emit(self, event: str, *, message: str = "", level: str = "INFO", task_log: bool = True, **fields: Any) -> None:
        """Append one fixed-field execution block to ``tool.log``.

        ``task_log`` remains a compatibility flag for existing adapters.  Raw
        serial line mirroring is intentionally suppressed because continuous
        device output belongs only in ``serial_logs/``.
        """
        if self.closed:
            return
        if event == "SERIAL_LINE" and not task_log:
            return
        block = self._tool_block(event, level, message or event, fields)
        with self._lock:
            self._tool.write(block)
            self._tool.flush()
        if task_log:
            print(f"{now_human()} [{level.upper()}] {message or event}", flush=True)

    def write_tool_log(self, name: str, content: str, *, append: bool = True) -> Path:
        """Compatibility bridge: external output is now a named tool.log event."""
        self.emit(
            "EXTERNAL_TOOL_OUTPUT",
            message=f"已记录外部工具输出：{Path(name).name}。",
            source=Path(name).name,
            raw=content,
            handling="append" if append else "replace-request-mapped-to-single-ledger",
        )
        return self.tool_log_path

    def append_tool_jsonl(self, name: str, payload: Mapping[str, Any]) -> Path:
        """Compatibility bridge for former per-tool JSONL writers."""
        self.emit(
            "TOOL_RECORD",
            message=f"已迁移专项记录：{Path(name).name}。",
            source=Path(name).name,
            raw=dict(payload),
        )
        return self.tool_log_path

    def emit_observation(
        self,
        channel: str,
        tags: Sequence[RawTag | Mapping[str, Any]],
        *,
        event: str = "OBSERVED_TAGS",
        normalized: Mapping[str, Any] | None = None,
        judgement: ToolJudgement | None = None,
        level: str = "INFO",
        task_log: bool = True,
        **fields: Any,
    ) -> dict[str, Any]:
        """同时保存设备原始标签和工具侧判断，并镜像关键行。"""
        raw_tags = raw_tags_from_mappings(tags)
        normalized_payload = dict(normalized or {})
        judgement_payload = judgement.to_dict() if judgement else {}
        message = render_observation(channel, raw_tags, normalized=normalized_payload, judgement=judgement)
        event_fields = {
            "channel": channel,
            "raw_tags": [item.to_dict() for item in raw_tags],
            "normalized": normalized_payload,
            "tool_judgement": judgement_payload,
            **fields,
        }
        if judgement:
            event_fields.update({
                "tool_status": judgement.tool_status,
                "tool_reason": judgement.tool_reason,
                "expected": dict(judgement.expected),
                "actual": dict(judgement.actual),
                "duration_ms": judgement.duration_ms,
                "evidence_refs": list(judgement.evidence_refs),
            })
        self.emit(event, message=message, level=level, task_log=task_log, **event_fields)
        return event_fields

    @staticmethod
    def _flatten_mapping(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _join_codes(values: Iterable[Any]) -> str:
        flattened: list[str] = []
        for value in values:
            if isinstance(value, str):
                flattened.append(value)
            elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, Mapping)):
                flattened.extend(str(item) for item in value)
            elif value is not None:
                flattened.append(str(value))
        return ";".join(sorted({item for item in flattened if item.strip()}))

    @staticmethod
    def _csv_value(value: Any) -> Any:
        if isinstance(value, (Mapping, list, tuple, set)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return value

    @property
    def cases_frozen(self) -> bool:
        return self._cases_frozen

    def require_cases_frozen(self, action: str) -> None:
        """Prevent a project adapter from changing its corpus after device I/O."""
        if not self._cases_frozen:
            message = f"执行 {action} 前必须先冻结 cases.csv。"
            self.emit("CASES_NOT_FROZEN", level="ERROR", message=message, phase="preflight", action_id=action)
            raise RuntimeError(message)

    def freeze_cases(
        self,
        cases: Iterable[CaseSpec | Mapping[str, Any]],
        *,
        random_seed: Any = "",
        profile_version: Any = "",
        profile_sha256: Any = "",
    ) -> int:
        """Freeze the final case order before the first device action."""
        if self._cases_frozen:
            raise RuntimeError("cases.csv is already frozen for this task")
        rows = list(cases)
        with self._lock:
            for order, value in enumerate(rows, start=1):
                if isinstance(value, CaseSpec):
                    metadata = dict(value.metadata)
                    row = {
                        "case_id": value.case_id,
                        "scenario": metadata.get("scenario") or value.domain or "",
                        "input_text": value.text,
                        "audio_path": value.audio_path or "",
                        "expected_offline_raw": metadata.get("expected_offline_raw", {}),
                        "expected_online_raw": metadata.get("expected_online_raw", {}),
                        "expected_wake_raw": metadata.get("expected_wake_raw", {}),
                        "accepted_raw_variants": metadata.get("accepted_raw_variants", {}),
                        "source_file": metadata.get("source_file", ""),
                        "source_sha256": metadata.get("source_sha256", ""),
                    }
                else:
                    raw = dict(value)
                    row = {
                        "case_id": raw.get("case_id", ""),
                        "scenario": raw.get("scenario", raw.get("domain", "")),
                        "input_text": raw.get("input_text", raw.get("text", "")),
                        "audio_path": raw.get("audio_path", ""),
                        "expected_wake_raw": raw.get("expected_wake_raw", {}),
                        "expected_offline_raw": raw.get("expected_offline_raw", {}),
                        "expected_online_raw": raw.get("expected_online_raw", {}),
                        "accepted_raw_variants": raw.get("accepted_raw_variants", {}),
                        "source_file": raw.get("source_file", ""),
                        "source_sha256": raw.get("source_sha256", ""),
                    }
                audio_path = Path(str(row["audio_path"])) if row["audio_path"] else None
                self._cases_writer.writerow({
                    "case_order": order,
                    **row,
                    "audio_sha256": sha256_file(audio_path) if audio_path and audio_path.is_file() else "",
                    "random_seed": random_seed,
                    "profile_version": profile_version,
                    "profile_sha256": profile_sha256,
                } | {name: self._csv_value(value) for name, value in row.items()})
            self._cases.flush()
        self._cases_frozen = True
        self._frozen_case_count = len(rows)
        self.emit(
            "CASES_FROZEN",
            message=f"已冻结本次压测用例，共 {len(rows)} 条。",
            phase="preflight",
            status="PASS",
            raw={"case_count": len(rows), "random_seed": random_seed, "profile_version": profile_version},
        )
        return len(rows)

    def set_epoch(self, epoch: int, *, reason: str = "") -> None:
        next_epoch = max(0, int(epoch))
        if next_epoch == self.current_epoch:
            return
        previous = self.current_epoch
        self.current_epoch = next_epoch
        self.emit(
            "SESSION_EPOCH_CHANGED",
            level="WARN" if previous else "INFO",
            message=f"设备会话 epoch 已切换为 {next_epoch}。",
            epoch=next_epoch,
            reason=reason,
            raw={"previous_epoch": previous, "current_epoch": next_epoch},
        )
        for listener in tuple(self._epoch_listeners):
            try:
                listener(next_epoch, reason=reason)
            except Exception as error:
                self.record_anomaly(
                    "EPOCH_LISTENER_EXCEPTION",
                    "设备会话切换通知失败。",
                    error=f"{type(error).__name__}: {error}",
                )

    def register_epoch_listener(self, listener: Any) -> None:
        if callable(listener):
            self._epoch_listeners.append(listener)

    def add_check(
        self,
        case_id: str,
        name: str,
        status: str,
        *,
        required: bool = True,
        reason: str = "",
        evidence: Iterable[str] = (),
        **fields: Any,
    ) -> dict[str, Any]:
        """Register a required/optional check used to derive a case status."""
        record = {
            "name": str(name),
            "status": str(status).upper(),
            "required": bool(required),
            "reason": reason,
            "evidence": [str(item) for item in evidence],
            **fields,
        }
        self.checks.setdefault(case_id, []).append(record)
        self.emit(
            "CASE_CHECK",
            level="ERROR" if record["status"] == "FAIL" else "INFO",
            message=f"用例检查 {name}: {record['status']}。",
            case_id=case_id,
            status=record["status"],
            reason=reason,
            evidence=record["evidence"],
            raw={"required": required, **fields},
        )
        return record

    def derive_case_status(self, case_id: str, fallback: str) -> tuple[str, list[dict[str, Any]]]:
        checks = list(self.checks.pop(case_id, ()))
        required = [item for item in checks if item["required"]]
        states = {item["status"] for item in required}
        if "FAIL" in states:
            return "FAIL", checks
        if "BLOCKED" in states:
            return "BLOCKED", checks
        if required and states <= {"PASS"}:
            return "PASS", checks
        return fallback, checks

    def configure_health_policy(self, policy: Mapping[str, Any] | None) -> None:
        self.health_policy = dict(policy or {})
        self.emit("HEALTH_POLICY", message="已加载连续异常健康策略。", raw=self.health_policy)

    def record_health(self, category: str, *, failed: bool, case_id: str = "", **fields: Any) -> dict[str, Any]:
        """Track continuous failures without stopping a runnable pressure task."""
        key = str(category).upper()
        count = self.health_streaks.get(key, 0) + 1 if failed else 0
        self.health_streaks[key] = count
        rule = self._flatten_mapping(self.health_policy.get(key) or self.health_policy.get(key.lower()))
        threshold = int(rule.get("threshold", 0) or 0)
        crossed = bool(failed and threshold > 0 and count >= threshold)
        record = {"category": key, "failed": failed, "consecutive_count": count, "threshold": threshold, "crossed": crossed}
        if crossed:
            handling = str(rule.get("handling") or "snapshot_and_continue")
            snapshot = {
                "case_id": case_id,
                "epoch": self.current_epoch,
                "category": key,
                "consecutive_count": count,
                "evidence": fields.get("evidence") or fields.get("evidence_refs") or [],
            }
            self.record_anomaly(
                f"HEALTH_{key}_THRESHOLD",
                f"连续 {count} 次 {key} 达到 profile 阈值。",
                case_id=case_id,
                handling=handling,
                **fields,
            )
            self.emit(
                "HEALTH_POLICY_TRIGGERED",
                level="WARN",
                message=f"连续异常达到阈值，执行策略：{handling}。",
                case_id=case_id,
                handling=handling,
                raw=record,
            )
            self.emit(
                "HEALTH_POLICY_SNAPSHOT",
                level="WARN",
                message="连续异常已记录当前证据快照。",
                case_id=case_id,
                phase="health_policy",
                handling=handling,
                raw=snapshot,
            )
            if "session" in handling or "recover" in handling:
                self.emit(
                    "HEALTH_SESSION_RECOVERY_REQUESTED",
                    level="WARN",
                    message="健康策略请求项目适配器恢复当前会话后继续执行。",
                    case_id=case_id,
                    phase="health_policy",
                    handling=handling,
                    raw=snapshot,
                )
            if bool(rule.get("stop", False)):
                self.stop.request(f"HEALTH_{key}_THRESHOLD")
        return record

    def record_case(self, result: CaseResult) -> None:
        initial_status = result.reviewed_status or result.raw_status
        status, checks = self.derive_case_status(result.case_id, initial_status)
        if status != initial_status:
            result.reviewed_status = status
            result.reason = "; ".join(filter(None, [result.reason, "required_check_matrix"]))
        result.facts = {**result.facts, "checks": checks, "epoch": self.current_epoch}
        self.counts[status] = self.counts.get(status, 0) + 1
        self.completed_cases += 1
        if status not in NORMAL_CASE_STATUSES:
            self.exception_counts[status] = self.exception_counts.get(status, 0) + 1
        facts = dict(result.facts)
        wake = self._flatten_mapping(facts.get("wake"))
        asr = self._flatten_mapping(facts.get("asr"))
        online = self._flatten_mapping(facts.get("online"))
        player = self._flatten_mapping(facts.get("player"))
        timing = self._flatten_mapping(facts.get("timing"))
        correlation = self._flatten_mapping(facts.get("correlation"))
        associations = list(facts.get("broadcast_recognition_associations", ()))
        raw_codes = facts.get("anomaly_codes", ())
        if isinstance(raw_codes, str):
            raw_codes = [raw_codes]
        anomaly_codes = [*raw_codes, *[item.get("reason", "") for item in associations if isinstance(item, Mapping) and item.get("status") != "PASS"]]
        check_summary = [{"name": item["name"], "status": item["status"], "required": item["required"]} for item in checks]
        with self._lock:
            row = {
                "round": self.completed_cases,
                "case_id": result.case_id,
                "started_at": facts.get("started_at", ""),
                "ended_at": now_iso(),
                "epoch": self.current_epoch,
                "scenario": facts.get("scenario", ""),
                "input_text": facts.get("input_text", ""),
                "audio_path": facts.get("audio_path", ""),
                "audio_sha256": facts.get("audio_sha256", ""),
                "broadcast_id": facts.get("broadcast_id", player.get("broadcast_id", "")),
                "wake_attempts": wake.get("attempts", facts.get("wake_attempts", "")),
                "wake_raw": wake.get("raw", wake.get("keyword", "")),
                "offline_attempts": asr.get("attempts", facts.get("offline_attempts", "")),
                "offline_keyword_raw": asr.get("keyword", ""),
                "offline_intent_raw": asr.get("intent", ""),
                "online_request_id": online.get("request_id", correlation.get("request_id", "")),
                "online_response_id": online.get("response_id", correlation.get("response_id", "")),
                "online_raw_response": online.get("raw", online.get("text", "")),
                "player_status": player.get("status", ""),
                "device_playback_status": player.get("device_playback_status", ""),
                "command_audio_duration_ms": timing.get("command_audio_duration_ms", ""),
                "e2e_latency_ms": timing.get("e2e_latency_ms", ""),
                "processing_latency_ms": timing.get("processing_latency_ms", ""),
                "recognition_latency_ms": timing.get("recognition_latency_ms", ""),
                "correlation_valid": correlation.get("valid", correlation.get("correlation_valid", "")),
                "raw_exact_status": facts.get("raw_exact_status", ""),
                "semantic_status": facts.get("semantic_status", ""),
                "checks_summary": json.dumps(check_summary, ensure_ascii=False),
                "raw_status": result.raw_status,
                "reviewed_status": result.reviewed_status or "",
                "final_status": status,
                "anomaly_codes": self._join_codes(anomaly_codes),
                "reason": result.reason,
                "evidence": self._join_codes(result.evidence),
                "facts_json": json.dumps(facts, ensure_ascii=False, sort_keys=True, default=str),
            }
            self._writer.writerow({name: self._csv_value(value) for name, value in row.items()})
            self._csv.flush()
        self.emit(
            "CASE_RESULT",
            message=f"用例 {result.case_id}: {status}；{result.reason}".rstrip("；"),
            case_id=result.case_id,
            round=self.completed_cases,
            epoch=self.current_epoch,
            status=status,
            reason=result.reason,
            raw={"checks": checks, "case": result.to_dict()},
            evidence=result.evidence,
        )
        self._record_round_exception_summary(result.case_id, status)

    def _record_round_exception_summary(self, case_id: str, status: str) -> None:
        """Print and persist the cumulative, task-level exception snapshot."""
        snapshot = {
            "completed_cases": self.completed_cases,
            "current_case_id": case_id,
            "current_status": status,
            "exception_counts": dict(sorted(self.exception_counts.items())),
            "exception_total": sum(self.exception_counts.values()),
            "anomaly_counts": dict(sorted(self.anomaly_counts.items())),
            "anomaly_total": sum(self.anomaly_counts.values()),
            "sticky_counts": self._sticky_counts(),
        }
        message = (
            f"[EXCEPTION_SUMMARY] round={self.completed_cases} "
            f"current={case_id}:{status} "
            f"cumulative={json.dumps(snapshot['exception_counts'], ensure_ascii=False)} "
            f"total={snapshot['exception_total']} "
            f"anomalies={json.dumps(snapshot['anomaly_counts'], ensure_ascii=False)} "
            f"anomaly_total={snapshot['anomaly_total']} "
            f"sticky={json.dumps(snapshot['sticky_counts'], ensure_ascii=False)}"
        )
        self.emit("ROUND_EXCEPTION_SUMMARY", message=message, round=self.completed_cases, raw=snapshot)

    def _sticky_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.sticky:
            code = str(item.get("code") or "UNKNOWN")
            counts[code] = counts.get(code, 0) + 1
        return dict(sorted(counts.items()))

    def update_progress(self, *, completed: int, target: int | None = None, **fields: Any) -> None:
        self.emit(
            "TASK_PROGRESS",
            message=f"当前进度：{completed}/{target if target is not None else '-'}。",
            round=completed,
            raw={
                "completed": completed,
                "target": target,
                "exception_counts": dict(sorted(self.exception_counts.items())),
                "anomaly_counts": dict(sorted(self.anomaly_counts.items())),
                **fields,
            },
        )

    def set_capability(self, name: str, status: str, reason: str = "", **fields: Any) -> None:
        self.capabilities[name] = {"status": status, "reason": reason, **fields}
        self.emit("CAPABILITY", message=f"能力 {name}: {status}；{reason}".rstrip("；"), task_log=True, capability=name, status=status, reason=reason, **fields)

    def add_sticky(self, code: str, message: str, **fields: Any) -> None:
        record = {"code": code, "message": message, "at": now_iso(), **fields}
        self.sticky.append(record)
        self.emit("STICKY_FATAL", level="ERROR", message=message, code=code, at=record["at"], **fields)

    def record_anomaly(self, code: str, message: str, **fields: Any) -> dict[str, Any]:
        """Record a recoverable task anomaly without hiding it behind a later pass."""
        normalized_code = str(code or "UNKNOWN_ANOMALY").strip().upper() or "UNKNOWN_ANOMALY"
        self.anomaly_counts[normalized_code] = self.anomaly_counts.get(normalized_code, 0) + 1
        record = {
            "code": normalized_code,
            "message": message,
            "at": now_iso(),
            "count": self.anomaly_counts[normalized_code],
            **fields,
        }
        self.emit("TASK_ANOMALY", level="ERROR", **record)
        return record

    def register_closer(self, name: str, closer: Any) -> None:
        """登记可调用的资源关闭函数，按逆序执行且每项有界。"""
        if callable(closer):
            self._closers.append((name, closer))

    def check_runtime(self) -> str | None:
        return self.stop.check(self.run_dir)

    def _reconcile_results(self) -> dict[str, Any]:
        """Recalculate final counts from the durable one-row-per-case ledger."""
        with self._lock:
            self._csv.flush()
        counts: dict[str, int] = {}
        rows: list[dict[str, str]] = []
        with self.results_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                rows.append(row)
                state = str(row.get("final_status") or "").upper()
                if state:
                    counts[state] = counts.get(state, 0) + 1
        invalid = sum(counts.get(value, 0) for value in ("BLOCKED", "ABORTED", "SKIPPED"))
        valid = max(0, len(rows) - invalid)
        passed = counts.get("PASS", 0) + counts.get("EXPECTED", 0)
        first_failure = next((row for row in rows if row.get("final_status") not in NORMAL_CASE_STATUSES), None)
        return {
            "rows": len(rows),
            "counts": counts,
            "invalid": invalid,
            "valid": valid,
            "passed": passed,
            "first_failure": first_failure,
            "success_rate": round((passed / valid) * 100, 4) if valid else None,
        }

    def finalize(self, status: str, reason: str) -> dict[str, Any]:
        if self.closed:
            return dict(self._final_summary or {"status": status, "reason": reason})
        for name, closer in reversed(self._closers):
            try:
                closer()
            except Exception as error:  # cleanup must remain visible, never hide the result
                self.add_sticky("TOOL_EXCEPTION", f"资源收尾失败: {name}", resource=name, error=str(error))
        reconciliation = self._reconcile_results()
        self.counts = dict(reconciliation["counts"])
        final_status = status
        final_reason = reason
        if self.counts.get("FAIL", 0) and final_status in {"PASS", "WARN", "PASS_WITH_WARNINGS"}:
            final_status = "FAIL"
            final_reason = f"{reason}；results.csv 含 {self.counts['FAIL']} 个 FAIL 轮次"
        elif self.counts.get("BLOCKED", 0) and not self.counts.get("PASS", 0) and final_status in {"PASS", "WARN", "PASS_WITH_WARNINGS"}:
            final_status = "BLOCKED"
            final_reason = f"{reason}；results.csv 没有有效 PASS 轮次且含 BLOCKED"
        if self.sticky and final_status in {"PASS", "WARN"}:
            final_status = "FAIL"
            final_reason = f"{reason}；存在 sticky fatal: {self.sticky[-1].get('code', 'UNKNOWN')}"
        completed_rows = reconciliation["rows"]
        invalid = reconciliation["invalid"]
        valid = reconciliation["valid"]
        passed = reconciliation["passed"]
        summary = {
            "status": final_status,
            "reason": final_reason,
            "ended_at": now_iso(),
            "counts": self.counts,
            "exception_counts": dict(sorted(self.exception_counts.items())),
            "exception_total": sum(self.exception_counts.values()),
            "anomaly_counts": dict(sorted(self.anomaly_counts.items())),
            "anomaly_total": sum(self.anomaly_counts.values()),
            "sticky_counts": self._sticky_counts(),
            "capabilities": self.capabilities,
            "sticky_failures": self.sticky,
            "result_directory": str(self.run_dir),
            "completed": completed_rows,
            "planned": self._frozen_case_count,
            "valid": valid,
            "success_rate": reconciliation["success_rate"],
            "first_failure": reconciliation["first_failure"],
        }
        self.emit(
            "TASK_FINISHED",
            message=f"任务结束: {final_status}；{final_reason}",
            status=final_status,
            reason=final_reason,
            raw=summary,
            handling="results.csv counts reconciled before final status",
        )
        for handle in (self._tool, self._csv, self._cases):
            if handle is not None:
                handle.close()
        self._final_summary = summary
        self.closed = True
        return summary

    def serial_log_path(self, port: str, role: str | None) -> Path:
        safe_port = str(port).replace("/", "_").replace("\\", "_")
        safe_role = str(role or "unknown").replace("/", "_").replace("\\", "_")
        return self.run_dir / "serial_logs" / f"serial_{safe_port}_{safe_role}.log"


class DeviceRuntime:
    """Small contract object; concrete serial/profile/playback adapters are injected."""

    def __init__(self, connection: ConnectionSpec, profile: Any, artifacts: TaskArtifacts):
        self.connection = connection
        self.profile = profile
        self.artifacts = artifacts
        self.started = False
        self.restart_recovery_monitor: Any | None = None

    def preflight(self) -> dict[str, Any]:
        self.artifacts.emit("PREFLIGHT", message="开始公共设备前置检查。", task_log=True, ports=self.connection.to_dict()["ports"])
        return {"ports": len(self.connection.ports), "profile": getattr(self.profile, "profile_id", None)}

    def start_restart_recovery(
        self,
        manager: Any,
        *,
        initialization_timeout_s: float = 10.0,
        poll_interval_s: float = 0.1,
        stable_for_s: float = 0.0,
    ) -> Any:
        """Continuously recover profile initialization after restart markers."""
        if self.restart_recovery_monitor is not None:
            return self.restart_recovery_monitor
        try:
            from .shell import ProfileRestartRecoveryMonitor
        except ImportError:  # direct execution fallback
            from shell import ProfileRestartRecoveryMonitor
        monitor = ProfileRestartRecoveryMonitor(
            self.profile,
            self.artifacts,
            manager,
            initialization_timeout_s=initialization_timeout_s,
            poll_interval_s=poll_interval_s,
            stable_for_s=stable_for_s,
        )
        monitor.start()
        self.restart_recovery_monitor = monitor
        self.started = True
        return monitor

    def recover_initialization(
        self,
        manager: Any,
        *,
        cursors: Mapping[str, int] | None = None,
        recovery_reason: str = "startup",
    ) -> dict[str, Any]:
        """Verify all profile initialization commands after a completed boot.

        A successful first recovery also starts background restart monitoring.
        Commands and validation rules always come from the project profile.
        """
        try:
            from .shell import ProfileRecoveryStateMachine
        except ImportError:  # direct execution fallback
            from shell import ProfileRecoveryStateMachine
        recovery = self.profile.recovery if hasattr(self.profile, "recovery") else {}
        timeout_s = max(0.1, float(recovery.get("initialization_timeout_s", 10.0)))
        poll_interval_s = max(0.02, float(recovery.get("restart_poll_interval_s", 0.1)))
        stable_for_s = max(0.0, float(recovery.get("stable_for_s", 0.0) or 0.0))
        result = ProfileRecoveryStateMachine(self.profile, self.artifacts, manager).run(
            timeout_s,
            cursors=cursors,
            recovery_reason=recovery_reason,
        )
        if result.get("status") == "PASS":
            self.start_restart_recovery(
                manager,
                initialization_timeout_s=timeout_s,
                poll_interval_s=poll_interval_s,
                stable_for_s=stable_for_s,
            )
        return result

    def check_ready(self) -> str | None:
        """Return a restart/recovery stop reason before a project starts new work."""
        return self.artifacts.check_runtime()

    def close(self) -> None:
        if self.restart_recovery_monitor is not None:
            self.restart_recovery_monitor.stop()
            self.restart_recovery_monitor = None
        if self.started:
            self.artifacts.emit("RUNTIME_CLOSE", message="公共运行时已收尾。", task_log=True)
            self.started = False
