"""lstest 结构化事件日志 v2：机器真源、人读时间线与确定性结论。

三层职责分开，互不替代：

``events.jsonl``  一行一个事件，固定 schema；``event_seq`` 递增，``raw`` 外置到
                 ``raw/``，只保留 path/sha256/bytes 引用。机器解析只读这个文件。
``tool.log``      完整动作审计：TOOL 与 DEVICE 状态按 host 毫秒排序，每个事件恰好一行，
                 含做法/前后状态/判断依据/证据定位；不写续行、不写摘要。
``summary.md/json``由 events 确定性生成的结论：双成功率、尝试分布、趋势、异常、
                 首败、责任层、gate 与最终判定分离。

旧版 29 字段平铺块（``time: ...`` / ``event: ...`` 逐行）仍可被
:func:`parse_legacy_flat` 读取，经 :func:`import_legacy_log` 迁移为 v2；旧运行目录的
``tool.log`` 四产物边界保持不变。

本模块不做任何设备动作，不访问网络。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:  # 包内导入；直接执行 scripts/*.py 时退化为同目录导入
    from . import states as state_catalog
    from . import result_layout
except ImportError:  # pragma: no cover - 直接执行回退
    import states as state_catalog  # type: ignore
    import result_layout  # type: ignore

BEIJING = timezone(timedelta(hours=8))

EVENT_SCHEMA = "lstest.event.v2"
SUMMARY_SCHEMA = "lstest.summary.v2"
BUNDLE_VERSION = 2

EVENT_FIELDS: tuple[str, ...] = (
    "schema", "event_seq", "time", "level", "event", "task_id", "round",
    "case_id", "phase", "source", "device", "port_role", "action_id",
    "broadcast_id", "correlation_id", "status", "reason", "attempt",
    "max_attempts", "elapsed_ms", "rule_id", "expected", "actual",
    "normalized", "handling", "evidence", "message", "details", "raw_ref",
    "legacy_warnings",
)
REQUIRED_FIELDS = ("schema", "event_seq", "time", "level", "event")

NUMERIC_FIELDS = {"event_seq", "round", "attempt", "max_attempts", "elapsed_ms"}
MAPPING_FIELDS = {"expected", "actual", "normalized", "details", "raw_ref"}

STATUS_SUCCESS = {"PASS", "EXPECTED", "OBSERVED", "INFO", "OK", "WARN"}
STATUS_FAILURE = {"FAIL", "ERROR", "BLOCKED", "DELIVERY_INCOMPLETE", "NEEDS_REVIEW", "TIMEOUT"}
ATTEMPT_STATUSES = {"NO_WAKE", "NO_WAKEUP", "RETRY", "MISS", "UNVERIFIED"}

TEST_OUTCOME_VALUES = ("PASS", "FAIL", "BLOCKED", "INCOMPLETE", "UNKNOWN")
DELIVERY_STATUS_VALUES = ("COMPLETE", "DELIVERY_INCOMPLETE", "BLOCKED", "UNKNOWN")
FINAL_STATUS_VALUES = ("PASS", "FAIL", "BLOCKED", "NEEDS_REVIEW")

#: gate 中表示“交付件缺失”的检查项；这些项为 false 时判定 DELIVERY_INCOMPLETE，
#: 而不是把测试结果也算成 FAIL。
ARTIFACT_GATE_CHECKS = {
    "structured_artifacts_present", "evidence_present", "receipt_present",
    "summary_present", "raw_evidence_present",
}
#: gate 中表示“前置条件不满足”的检查项。
BLOCKING_GATE_CHECKS = {
    "prerequisites_met", "infrastructure_ready", "device_available",
    "input_present", "profile_contract_met",
    # 状态目录 gate：声明的必需状态缺失、或阻断状态未恢复，都不得签发 PASS。
    "required_states_present", "no_unrecovered_blocking",
}

TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}$")
ABS_WIN_RE = re.compile(r"[A-Za-z]:[\\/]")
ABS_POSIX_RE = re.compile(r"(?<![\w.-])/(?:home|root|Users|tmp|mnt|var)/")

TREND_THRESHOLD_PCT = 10.0
DETAILS_INLINE_LIMIT = 4096
EVIDENCE_PATH_LIMIT = 240


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def now_human() -> str:
    """北京时间、无时区、毫秒。设备层与记录层共用的唯一文本时间格式。"""
    return datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _skip(value: Any) -> bool:
    """空值判定：None / 空串 / 空容器 / 旧日志占位符 ``-`` 都视为缺失。"""
    return value is None or value == "" or value == [] or value == {} or value == "-"


def _num(value: Any) -> Any:
    if _skip(value):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _text(value: Any) -> str:
    if _skip(value):
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, (int,)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return ",".join(_text(item) for item in value)
    if isinstance(value, Mapping):
        pairs = [f"{key}={_text(item)}" for key, item in value.items() if not _skip(item)]
        return " ".join(pairs) if pairs else "-"
    return " ".join(str(value).split())


def sanitize_path(value: Any) -> str | None:
    """把绝对路径收敛成可公开的相对路径或文件名。

    规则固定且可复核：命中 ``result/`` 时保留其结果目录之后的相对路径；否则只保留
    文件名。旧日志里的绝对路径不会进入 events.jsonl、tool.log 或 summary。
    """
    if _skip(value):
        return None
    text = str(value).strip().replace("\\", "/")
    if "result/" in text:
        return text.split("result/", 1)[1].lstrip("/")
    if text.startswith("/") or ABS_WIN_RE.match(text):
        return text.rsplit("/", 1)[-1] or None
    return text[:EVIDENCE_PATH_LIMIT]


def normalize_evidence(value: Any) -> list[dict[str, Any]]:
    """统一 evidence 形状：``[{path, line?, sha256?, external?}]``。

    旧日志支持 ``"serial_logs/x.log#000123"`` 这种字符串引用。
    """
    if _skip(value):
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    refs: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, Mapping):
            path = item.get("path") or item.get("file")
            ref: dict[str, Any] = {"path": sanitize_path(path)}
            line = _num(item.get("line"))
            if line is not None:
                ref["line"] = int(line)
            if item.get("sha256"):
                ref["sha256"] = str(item["sha256"]).lower()
            if item.get("external"):
                ref["external"] = True
            if ref["path"]:
                refs.append(ref)
            continue
        text = str(item).strip()
        if not text:
            continue
        line = None
        if "#" in text:
            text, _, tail = text.rpartition("#")
            line = _num(tail)
        ref = {"path": sanitize_path(text)}
        if line is not None:
            ref["line"] = int(line)
        if ref["path"]:
            refs.append(ref)
    return refs


def _sanitize_details(value: Any, depth: int = 0) -> Any:
    """递归把 details 里的路径字段收敛为相对路径/文件名（公开产物不含绝对路径）。"""
    if depth > 4:
        return value
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            text_key = str(key).lower()
            if isinstance(item, str) and (
                ABS_WIN_RE.match(item) or item.startswith("/")
                or any(
                    text_key.endswith(token) or text_key == token
                    for token in ("path", "file", "dir", "cwd", "workdir", "output", "input", "_root")
                )
            ):
                result[key] = sanitize_path(item)
            else:
                result[key] = _sanitize_details(item, depth + 1)
        return result
    if isinstance(value, list):
        return [_sanitize_details(item, depth + 1) for item in value]
    return value


def derive_correlation(round_value: Any, case_id: Any, action_id: Any) -> str | None:
    parts: list[str] = []
    if not _skip(round_value):
        parts.append(f"R{_text(round_value)}")
    if not _skip(case_id):
        parts.append(str(case_id))
    if not _skip(action_id):
        parts.append(str(action_id))
    return ":".join(parts) if len(parts) >= 2 else None


# ---------------------------------------------------------------------------
# 事件模型
# ---------------------------------------------------------------------------

@dataclass
class EventRecord:
    """一个 v2 事件。字段顺序即 JSONL 输出顺序，保证字节级可复现。"""

    event: str
    time: str | None = None
    level: str = "INFO"
    task_id: str | None = None
    event_seq: int | None = None
    round: Any = None
    case_id: str | None = None
    phase: str | None = None
    source: str | None = None
    device: str | None = None
    port_role: str | None = None
    action_id: str | None = None
    broadcast_id: str | None = None
    correlation_id: str | None = None
    status: str | None = None
    reason: str | None = None
    attempt: Any = None
    max_attempts: Any = None
    elapsed_ms: Any = None
    rule_id: str | None = None
    expected: Any = None
    actual: Any = None
    normalized: Any = None
    handling: str | None = None
    evidence: list[dict[str, Any]] | None = None
    message: str | None = None
    details: Any = None
    raw_ref: Any = None
    legacy_warnings: list[str] | None = None
    #: 迁移/派生阶段的原始 raw 内容；不写入 events.jsonl，只用于提取设备事件。
    raw_payload: Any = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": EVENT_SCHEMA,
            "event_seq": self.event_seq,
            "time": self.time,
            "level": (self.level or "INFO").upper(),
            "event": (self.event or "UNKNOWN").upper(),
        }
        payload.update({
            "task_id": self.task_id,
            "round": self.round,
            "case_id": self.case_id,
            "phase": self.phase,
            "source": self.source,
            "device": self.device,
            "port_role": self.port_role,
            "action_id": self.action_id,
            "broadcast_id": self.broadcast_id,
            "correlation_id": self.correlation_id,
            "status": (self.status or None) and str(self.status).upper(),
            "reason": self.reason,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "elapsed_ms": self.elapsed_ms,
            "rule_id": self.rule_id,
            "expected": self.expected,
            "actual": self.actual,
            "normalized": self.normalized,
            "handling": self.handling,
            "evidence": list(self.evidence or []),
            "message": self.message,
            "details": self.details,
            "raw_ref": self.raw_ref,
        })
        if self.legacy_warnings:
            payload["legacy_warnings"] = list(self.legacy_warnings)
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=False)


def record_from_mapping(payload: Mapping[str, Any], *, seq: int | None = None) -> EventRecord:
    """把任意 mapping（v2 事件 / 旧 29 字段块 / 运行时 emit 字段）转为事件。

    只搬运已知字段，未知字段放进 ``details`` 的 ``legacy_fields``，不静默丢弃。
    """
    warnings: list[str] = []
    values = dict(payload)
    raw_payload = values.pop("raw", None)
    known = set(EVENT_FIELDS) | {"schema", "legacy_fields"}
    unknown = {key: values.pop(key) for key in list(values) if key not in known and not _skip(values.get(key))}

    round_value = _num(values.get("round"))
    attempt = _num(values.get("attempt"))
    max_attempts = _num(values.get("max_attempts"))
    elapsed = _num(values.get("elapsed_ms"))
    for name, raw in (("round", values.get("round")), ("attempt", values.get("attempt")),
                      ("max_attempts", values.get("max_attempts")), ("elapsed_ms", values.get("elapsed_ms"))):
        if not _skip(raw) and _num(raw) is None:
            warnings.append(f"{name} 不是数值: {raw!r}")

    details = values.get("details")
    if unknown:
        details = dict(details) if isinstance(details, Mapping) else {"value": details}
        details.setdefault("legacy_fields", unknown)
    if details is not None:
        details = _sanitize_details(details)
    details_size = len(json.dumps(details, ensure_ascii=False, default=str)) if details is not None else 0
    if details_size > DETAILS_INLINE_LIMIT:
        warnings.append(f"details 超过 {DETAILS_INLINE_LIMIT} 字节，v2 要求外置或裁减")

    record = EventRecord(
        event=str(values.get("event") or "UNKNOWN").upper(),
        time=ensure_ms(values.get("time")),
        level=str(values.get("level") or "INFO").upper(),
        task_id=None if _skip(values.get("task_id")) else str(values.get("task_id")),
        event_seq=int(seq if seq is not None else (_num(values.get("event_seq")) or 0) or 0) or None,
        round=round_value,
        case_id=None if _skip(values.get("case_id")) else str(values.get("case_id")),
        phase=None if _skip(values.get("phase")) else str(values.get("phase")),
        source=None if _skip(values.get("source")) else str(values.get("source")),
        device=None if _skip(values.get("device")) else str(values.get("device")),
        port_role=None if _skip(values.get("port_role")) else str(values.get("port_role")),
        action_id=None if _skip(values.get("action_id")) else str(values.get("action_id")),
        broadcast_id=None if _skip(values.get("broadcast_id")) else str(values.get("broadcast_id")),
        correlation_id=None if _skip(values.get("correlation_id")) else str(values.get("correlation_id")),
        status=None if _skip(values.get("status")) else str(values.get("status")).upper(),
        reason=None if _skip(values.get("reason")) else str(values.get("reason")),
        attempt=attempt,
        max_attempts=max_attempts,
        elapsed_ms=elapsed,
        rule_id=None if _skip(values.get("rule_id")) else str(values.get("rule_id")),
        expected=None if _skip(values.get("expected")) else values.get("expected"),
        actual=None if _skip(values.get("actual")) else values.get("actual"),
        normalized=None if _skip(values.get("normalized")) else values.get("normalized"),
        handling=None if _skip(values.get("handling")) else str(values.get("handling")),
        evidence=normalize_evidence(values.get("evidence")),
        message=None if _skip(values.get("message")) else " ".join(str(values.get("message")).split()),
        details=None if details is None else details,
        raw_ref=values.get("raw_ref") if isinstance(values.get("raw_ref"), Mapping) else None,
        legacy_warnings=warnings or None,
        raw_payload=raw_payload,
    )
    if record.correlation_id is None:
        record.correlation_id = derive_correlation(record.round, record.case_id, record.broadcast_id or record.action_id)
    return record


# ---------------------------------------------------------------------------
# 旧版 29 字段平铺块解析
# ---------------------------------------------------------------------------

_LEGACY_KEY_RE = re.compile(r"^([a-z][a-z0-9_]*):\s?(.*)$")


class LegacyFormatError(ValueError):
    """旧日志结构错误；消息包含行号，便于定位真实文件。"""


def _legacy_value(raw: str, line_no: int, warnings: list[str]) -> Any:
    text = raw.strip()
    if text in {"", "-"}:
        return None
    if text.startswith(("{", "[", '"')) or text in {"true", "false", "null"} or re.fullmatch(r"-?\d+(\.\d+)?", text):
        try:
            return json.loads(text)
        except ValueError:
            warnings.append(f"第 {line_no} 行 JSON 解析失败，保留原始文本")
            return text
    return text


def _json_incomplete(raw: str) -> bool:
    text = raw.lstrip()
    if not text.startswith(("{", "[")):
        return False
    try:
        json.loads(text)
    except ValueError:
        return True
    return False


def parse_legacy_flat(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """解析旧版平铺块日志，返回 ``(fields 列表, 全局警告)``。

    容忍：CRLF / BOM / 末尾无空行 / 空行缺失 / 多行 JSON / ``-`` 空值 /
    中文 / Windows 绝对路径 / 未知字段 / 缺失字段。缺失 ``event`` 的记录保留为
    ``UNKNOWN`` 并记警告，不丢事件。格式完全不可识别时抛 :class:`LegacyFormatError`。
    """
    warnings: list[str] = []
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    blocks: list[list[tuple[int, str, str]]] = []
    current: list[tuple[int, str, str]] = []

    def flush() -> None:
        if current:
            blocks.append(list(current))
            current.clear()

    for line_no, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            flush()
            continue
        match = _LEGACY_KEY_RE.match(line)
        if match and match.group(1) == "time" and any(key == "time" for _, key, _ in current):
            # 旧日志可能漏掉块间空行：出现第二个 time 即开始新块。
            flush()
        if match:
            key, raw_value = match.groups()
            current.append((line_no, key, raw_value))
        elif current and (line[:1].isspace() or _json_incomplete(current[-1][2])):
            # 多行 JSON 的续行：先拼接原始文本，块结束后再统一解析。
            prev_no, key, prev_raw = current[-1]
            current[-1] = (prev_no, key, prev_raw + "\n" + line.rstrip())
        elif current:
            warnings.append(f"第 {line_no} 行不是 key: value，已忽略")
        else:
            warnings.append(f"第 {line_no} 行缺少所属事件块，已忽略")
    flush()

    records: list[dict[str, Any]] = []
    for block in blocks:
        payload: dict[str, Any] = {}
        block_warnings: list[str] = []
        for line_no, key, raw_value in block:
            payload[key] = _legacy_value(raw_value, line_no, block_warnings)
        if not payload:
            continue
        records.append(payload)
        warnings.extend(block_warnings)
    if not records:
        raise LegacyFormatError("未找到任何 'time:'/'event:' 平铺块；不是旧版结构化日志")
    if not any(
        any(field in item for field in ("time", "level", "event", "task_id")) for item in records
    ):
        raise LegacyFormatError("没有任何 time/level/event/task_id 字段；不是旧版结构化日志")
    return records, warnings


def parse_legacy_file(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    return parse_legacy_flat(Path(path).read_text(encoding="utf-8", errors="replace"))


# ---------------------------------------------------------------------------
# 写入器
# ---------------------------------------------------------------------------

class EventLogWriter:
    """v2 事件写入器：框架侧唯一写入口，适配器不得自行拼 JSONL。

    默认按结果布局 v3 落盘：``events/events.jsonl`` 与 ``evidence/raw/``；
    ``finalize()`` 再生成 ``events/tool.log``、``summary.md``、``reports/summary.json``
    与根目录 ``README.md``/``manifest.json``。``layout="v2"`` 保持旧根目录布局。
    写入即 flush+fsync，标准证据（含崩溃中断）不会因为进程退出而丢失。
    """

    def __init__(self, run_dir: Path, task_id: str, *, raw_min_bytes: int = 0,
                 fsync: bool = True, layout: str = result_layout.LAYOUT_V3):
        self.run_dir = Path(run_dir)
        self.layout = layout
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir = result_layout.path(self.run_dir, "raw_dir", layout)
        self.task_id = task_id
        self.raw_min_bytes = raw_min_bytes
        self.fsync = fsync
        self.events_path = result_layout.path(self.run_dir, "events", layout)
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.events_path.open("w", encoding="utf-8", newline="\n")
        self._seq = 0
        self._closed = False

    def emit(self, event: str, **fields: Any) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("EventLogWriter 已关闭")
        self._seq += 1
        payload = dict(fields)
        raw = payload.pop("raw", None)
        payload.setdefault("task_id", self.task_id)
        payload.setdefault("time", now_human())
        payload["event"] = event
        record = record_from_mapping(payload, seq=self._seq)
        record.raw_payload = raw
        if raw is not None and not _skip(raw):
            record.raw_ref = self._externalize_raw(record, raw)
        record.event_seq = self._seq
        self._handle.write(record.to_json() + "\n")
        self._handle.flush()
        if not self.fsync:
            return record.to_dict()
        try:  # fsync 在部分文件系统上不可用，退化为 flush
            import os
            os.fsync(self._handle.fileno())
        except OSError:  # pragma: no cover - 平台差异
            pass
        return record.to_dict()

    def _externalize_raw(self, record: EventRecord, raw: Any) -> dict[str, Any]:
        payload = json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True, default=str).encode("utf-8")
        if len(payload) < self.raw_min_bytes:
            return {}
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        name = f"e{record.event_seq:04d}_{record.event}.json"
        target = self.raw_dir / name
        target.write_bytes(payload)
        return {
            "path": f"{self.raw_dir.relative_to(self.run_dir).as_posix()}/{name}",
            "sha256": sha256_bytes(payload),
            "bytes": len(payload),
            "lines": payload.count(b"\n") + (0 if payload.endswith(b"\n") else 1),
        }

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._handle.close()

    def finalize(self, meta: Mapping[str, Any] | None = None,
                 extension: "state_catalog.StateExtension | None" = None) -> dict[str, Any]:
        """关闭写入并生成三层产物，返回 summary.json 内容。"""
        self.close()
        records = read_events(self.events_path)
        return write_bundle(records, self.run_dir, meta=meta, events_path=self.events_path,
                            extension=extension, layout=self.layout)


def write_event_records(records: Sequence[EventRecord], path: Path) -> int:
    """把内存中的事件写成 events.jsonl（按给定顺序重新编号）。

    供迁移/测试/适配器离线构建使用；在线运行请用 :class:`EventLogWriter`。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for index, record in enumerate(records, start=1):
            record.event_seq = index
            handle.write(record.to_json() + "\n")
    return len(records)


def read_events(path: Path) -> list[EventRecord]:
    """读取 events.jsonl；空行忽略，坏行给出物理行号。"""
    records: list[EventRecord] = []
    for line_no, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except ValueError as error:
            raise LegacyFormatError(f"{path}:{line_no} 不是合法 JSON: {error}") from error
        if not isinstance(payload, Mapping):
            raise LegacyFormatError(f"{path}:{line_no} 不是 JSON 对象")
        record = record_from_mapping(payload, seq=_num(payload.get("event_seq")) or len(records) + 1)
        records.append(record)
    return records


def _promote_raw_context(payload: Mapping[str, Any]) -> dict[str, Any]:
    """旧日志的 node/user/python/platform/cwd 在 raw 里；把它们提升到 details.environment。

    只搬运上下文键，raw 原文仍完整外置保留，因此可复核、可回滚。
    """
    promoted = dict(payload)
    raw = payload.get("raw")
    details = payload.get("details")
    details = dict(details) if isinstance(details, Mapping) else {}
    if isinstance(raw, Mapping):
        keys = ("node", "user", "python", "platform", "cwd", "pid")
        environment = {key: raw[key] for key in keys if not _skip(raw.get(key))}
        if environment:
            existing = details.get("environment") if isinstance(details.get("environment"), Mapping) else {}
            details["environment"] = {**environment, **dict(existing)}
    if details:
        promoted["details"] = details
    return promoted


def _sort_records(records: Sequence[EventRecord]) -> list[EventRecord]:
    """按 host 毫秒稳定排序；缺失时间排末尾，保持原相对顺序。"""
    return [
        record for _, record in sorted(
            enumerate(records),
            key=lambda pair: (ensure_ms(pair[1].time) or "9999-12-31 23:59:59.999", pair[0]),
        )
    ]


def import_legacy_log(
    source: Path,
    out_dir: Path,
    *,
    task_id: str | None = None,
    raw_min_bytes: int = 0,
    meta: Mapping[str, Any] | None = None,
    markers: Mapping[str, Any] | str | Path | None = None,
    extension: "state_catalog.StateExtension | None" = None,
    layout: str = result_layout.LAYOUT_V3,
) -> dict[str, Any]:
    """旧平铺块日志 → v2 事件 + v3 结果目录；不改动源文件，不联网。

    ``markers`` 是项目提供的设备 marker 表（见 ``references/structured-event-log-v2.md``）；
    提供后会把 raw 中的设备状态派生成独立 DEVICE 事件，与 TOOL 事件同一毫秒时序。
    """
    source = Path(source)
    payloads, warnings = parse_legacy_file(source)
    out_dir = Path(out_dir)
    task = task_id or _guess_task_id(payloads) or source.stem
    records: list[EventRecord] = []
    for item in payloads:
        prepared = _promote_raw_context(item)
        records.append(record_from_mapping(prepared, seq=len(records) + 1))
    marker_value = markers if markers is not None else (dict(meta or {}).get("device_markers"))
    device_markers, ts_pattern = load_markers(marker_value)
    round_power_on: dict[Any, str] = {}
    for record in records:
        actual = record.actual if isinstance(record.actual, Mapping) else {}
        power_on = ensure_ms(actual.get("power_on_at"))
        if power_on and record.round is not None:
            round_power_on.setdefault(record.round, power_on)
    device_events = derive_device_events(
        records, device_markers, device_ts_pattern=ts_pattern, round_power_on=round_power_on,
        extension=extension,
    )
    records = _sort_records(records + device_events)
    writer = EventLogWriter(out_dir, task, layout=layout)
    try:
        for record in records:
            payload = record.to_dict()
            payload.pop("schema", None)
            payload["event"] = record.event
            payload["raw"] = record.raw_payload
            writer.emit(record.event, **{key: value for key, value in payload.items() if key != "event"})
    finally:
        writer.close()
    summary = writer.finalize(meta=_merge_meta(meta, source=source, import_warnings=warnings),
                              extension=extension)
    return summary


def _guess_task_id(payloads: Sequence[Mapping[str, Any]]) -> str | None:
    for item in payloads:
        if item.get("task_id"):
            return str(item["task_id"])
    return None


def _merge_meta(meta: Mapping[str, Any] | None, *, source: Path | None = None,
                import_warnings: Sequence[str] = ()) -> dict[str, Any]:
    merged = dict(meta or {})
    if source is not None:
        merged.setdefault("import", {})
        if isinstance(merged["import"], Mapping):
            merged["import"] = dict(merged["import"])
            merged["import"].update({
                "source_file": sanitize_path(source.name),
                "source_sha256": sha256_file(source),
                "source_bytes": source.stat().st_size,
            })
    if import_warnings:
        merged["import_warnings"] = list(import_warnings)
    return merged


# ---------------------------------------------------------------------------
# 统计与判定
# ---------------------------------------------------------------------------

def _value(record: EventRecord, *names: str) -> Any:
    for name in names:
        candidate = getattr(record, name, None)
        if not _skip(candidate):
            return candidate
    return None


def _actual(record: EventRecord, *names: str) -> Any:
    for container in (record.actual, record.normalized, record.details):
        if isinstance(container, Mapping):
            for name in names:
                candidate = container.get(name)
                if not _skip(candidate):
                    return candidate
    return None


def _end_record(records: Sequence[EventRecord]) -> EventRecord | None:
    """任务收尾事件：项目工具用 TASK_END，框架运行时用 TASK_FINISHED。"""
    for record in reversed(records):
        if record.event in {"TASK_END", "TASK_FINISHED"}:
            return record
    return None


def _rounds(records: Sequence[EventRecord]) -> list[dict[str, Any]]:
    """按 ROUND_RESULT/CASE_RESULT 汇总轮次；没有则返回空（不猜轮次）。"""
    rounds: list[dict[str, Any]] = []
    for record in records:
        if record.event not in {"ROUND_RESULT", "CASE_RESULT"}:
            continue
        actual = record.actual if isinstance(record.actual, Mapping) else {}
        details = record.details if isinstance(record.details, Mapping) else {}
        attempts = _num(actual.get("attempts_total")) or _num(details.get("attempts_total"))
        first = _num(actual.get("first_success_attempt")) or _num(details.get("first_success_attempt"))
        if first is None and attempts is not None:
            # 无显式首次成功次数时，只有“一次尝试即成功”才等价于第 1 次成功。
            first = 1 if attempts == 1 and str(record.status).upper() == "PASS" else None
        rounds.append({
            "round": _num(record.round),
            "case_id": record.case_id,
            "status": (record.status or "UNKNOWN").upper(),
            "reason": record.reason,
            "attempts_total": attempts,
            "first_success_attempt": first,
            "no_wakeup_attempts": actual.get("no_wakeup_attempts"),
            "first_play_start_offset_s": _num(actual.get("first_play_start_offset_s")),
            "recording_start_offset_s": _num(actual.get("recording_start_offset_s")),
            "first_wakeup_offset_s": _num(actual.get("first_wakeup_offset_s")),
            "elapsed_ms": _num(record.elapsed_ms),
            "event_seq": record.event_seq,
            "evidence": [item.get("path") for item in (record.evidence or [])],
        })
    if rounds:
        rounds.sort(key=lambda item: (item["round"] if item["round"] is not None else 1 << 30, item["event_seq"] or 0))
    return rounds


def _target_rounds(records: Sequence[EventRecord]) -> Any:
    for record in records:
        for container in (record.expected, record.actual, record.details):
            if isinstance(container, Mapping) and not _skip(container.get("target_rounds")):
                return _num(container["target_rounds"])
    return None


def _milestone(records: Sequence[EventRecord]) -> dict[str, Any]:
    """最后一个 MILESTONE 的增量统计；没有则空。"""
    last: Mapping[str, Any] = {}
    for record in records:
        if record.event == "MILESTONE" and isinstance(record.actual, Mapping):
            last = record.actual
    if not last:
        return {}
    aggregate = last.get("aggregate") if isinstance(last.get("aggregate"), Mapping) else {}
    counters = last.get("counters") if isinstance(last.get("counters"), Mapping) else {}
    return {
        "event_seq": next((record.event_seq for record in reversed(records) if record.event == "MILESTONE"), None),
        "completed_rounds": _num(last.get("completed_rounds")),
        "target_rounds": _num(last.get("target_rounds")),
        "avg_sec_per_round": _num(last.get("avg_sec_per_round")),
        "recent_elapsed_sec": _num(last.get("recent_10_elapsed_sec")),
        "free_gb": _num(last.get("free_gb")),
        "counters": dict(counters),
        "aggregate": dict(aggregate),
    }


def _gate(records: Sequence[EventRecord]) -> dict[str, Any] | None:
    for record in reversed(records):
        if not isinstance(record.actual, Mapping):
            continue
        gate = record.actual.get("gate")
        if isinstance(gate, Mapping):
            nested = gate.get("checks") if isinstance(gate.get("checks"), Mapping) else gate
            checks = {str(key): value for key, value in nested.items()
                      if isinstance(value, bool) and key not in {"gate_pass"}}
            failed = sorted(key for key, value in checks.items() if value is False)
            return {
                "source_event": record.event,
                "source_event_seq": record.event_seq,
                "recorded_status": (record.status or "").upper() or None,
                "checks": dict(sorted(checks.items())),
                "gate_pass": bool(gate.get("gate_pass")) if isinstance(gate.get("gate_pass"), bool) else None,
                "failed_checks": failed,
                "evaluated_at": gate.get("evaluated_at"),
            }
    return None


def _distribution(rounds: Sequence[dict[str, Any]], max_attempts: Any) -> dict[str, Any]:
    limit = int(_num(max_attempts) or 10)
    counts = {str(index): 0 for index in range(1, max(limit, 10) + 1)}
    counts["none"] = 0
    for item in rounds:
        first = item.get("first_success_attempt")
        if first is None:
            counts["none"] += 1
        else:
            key = str(int(first))
            counts[key] = counts.get(key, 0) + 1
    total = len(rounds)
    percent = {key: round(count * 100.0 / total, 1) if total else 0.0 for key, count in counts.items()}
    peak = max(counts.values()) if counts else 0
    low = min(counts.values()) if counts else 0
    return {
        "counts": counts,
        "percent": percent,
        "completed_rounds": total,
        "max_count_keys": sorted(key for key, count in counts.items() if count == peak) if total else [],
        "min_count_keys": sorted(key for key, count in counts.items() if count == low) if total else [],
    }


def _trend(values: Sequence[float | int | None], *, label: str) -> dict[str, Any]:
    series = [float(item) for item in values if item is not None]
    payload = {
        "label": label,
        "count": len(series),
        "first": series[0] if series else None,
        "last": series[-1] if series else None,
        "min": min(series) if series else None,
        "max": max(series) if series else None,
        "mean": round(sum(series) / len(series), 3) if series else None,
        "direction": "insufficient",
        "delta_pct": None,
    }
    if len(series) >= 4:
        half = len(series) // 2
        first_half = sum(series[:half]) / half
        second_half = sum(series[half:]) / (len(series) - half)
        if first_half:
            delta = (second_half - first_half) / first_half * 100.0
            payload["delta_pct"] = round(delta, 1)
            if delta > TREND_THRESHOLD_PCT:
                payload["direction"] = "degrading"
            elif delta < -TREND_THRESHOLD_PCT:
                payload["direction"] = "improving"
            else:
                payload["direction"] = "stable"
    return payload


def _anomalies(records: Sequence[EventRecord], rounds: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        status = (record.status or "").upper()
        if status in STATUS_SUCCESS or not status:
            continue
        key = (status, record.event)
        bucket = buckets.setdefault(key, {
            "code": status,
            "event": record.event,
            "count": 0,
            "rounds": [],
            "attempts": [],
            "first_seq": record.event_seq,
            "last_seq": record.event_seq,
            "example": record.message,
            "reason": record.reason,
            "handling": record.handling,
            "evidence": [],
        })
        bucket["count"] += 1
        bucket["last_seq"] = record.event_seq
        if record.round is not None and record.round not in bucket["rounds"]:
            bucket["rounds"].append(record.round)
        if record.attempt is not None:
            bucket["attempts"].append(record.attempt)
        for item in record.evidence or []:
            if item.get("path") and item["path"] not in bucket["evidence"]:
                bucket["evidence"].append(item["path"])
        if not bucket["example"] and record.message:
            bucket["example"] = record.message

    anomalies: list[dict[str, Any]] = []
    for bucket in sorted(buckets.values(), key=lambda item: item["first_seq"] or 0):
        affected = [item for item in rounds if item["round"] in bucket["rounds"]]
        recovered = bool(affected) and all(item["status"] == "PASS" for item in affected)
        total = len(rounds) or 1
        anomalies.append({
            "code": bucket["code"],
            "event": bucket["event"],
            "count": bucket["count"],
            "rounds": sorted(item for item in bucket["rounds"] if item is not None),
            "round_rate_pct": round(len(bucket["rounds"]) * 100.0 / total, 1),
            "attempts": bucket["attempts"],
            "recovered": recovered,
            "recovery": "全部相关轮次最终 PASS" if recovered else (
                "存在未恢复轮次" if affected else "无对应轮次结果"
            ),
            "reason": bucket["reason"],
            "handling": bucket["handling"],
            "example": bucket["example"],
            "evidence": bucket["evidence"][:5],
            "first_event_seq": bucket["first_seq"],
            "last_event_seq": bucket["last_seq"],
        })
    return anomalies


def _latency(records: Sequence[EventRecord], rounds: Sequence[dict[str, Any]]) -> dict[str, Any]:
    pdu = [_num(record.elapsed_ms) for record in records if record.event == "PDU_CYCLE"]
    recordings = [_num(record.elapsed_ms) for record in records if record.event == "RECORDING"]
    milestone = _milestone(records)
    per_round = []
    pdu_by_round = {}
    for record in records:
        if record.event == "PDU_CYCLE" and record.round is not None:
            pdu_by_round[record.round] = record.time
    for item in rounds:
        start = pdu_by_round.get(item["round"])
        end = next((record.time for record in records
                    if record.event == "ROUND_RESULT" and record.round == item["round"]), None)
        if start and end:
            try:
                delta = (datetime.strptime(end, "%Y-%m-%d %H:%M:%S.%f")
                         - datetime.strptime(start, "%Y-%m-%d %H:%M:%S.%f")).total_seconds()
                per_round.append(round(delta, 3))
            except ValueError:
                continue
    return {
        "pdu_cycle_ms": _trend(pdu, label="PDU 断电-上电耗时"),
        "recording_ms": _trend(recordings, label="本轮录音时长"),
        "round_seconds": _trend(per_round, label="每轮耗时（PDU 到轮末）"),
        "first_wakeup_offset_s": _trend([item.get("first_wakeup_offset_s") for item in rounds], label="首次唤醒相对秒数"),
        "recording_start_offset_s": _trend([item.get("recording_start_offset_s") for item in rounds], label="开始录音相对秒数"),
        "first_play_start_offset_s": _trend([item.get("first_play_start_offset_s") for item in rounds], label="首次播报相对秒数"),
        "avg_sec_per_round": milestone.get("avg_sec_per_round"),
        "recent_elapsed_sec": milestone.get("recent_elapsed_sec"),
        "free_gb": milestone.get("free_gb"),
    }


def _first_failure(rounds: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    for item in rounds:
        if item["status"] in STATUS_FAILURE:
            return dict(item)
    return None


def _worst_round(rounds: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    scored = [item for item in rounds if item.get("attempts_total") is not None]
    if not scored:
        return None
    worst = max(scored, key=lambda item: (item["attempts_total"], -(item["round"] or 0)))
    return dict(worst)


def _responsibility(rounds: Sequence[dict[str, Any]], gate: Mapping[str, Any] | None,
                    anomalies: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """责任层只做可复核的规则判定，不猜测。

    优先级：PDU/台架失败 → bench；串口/录音失败或无设备响应 → device；
    仅交付件缺失 → tool；其余异常保持 unknown 并列出待隔离变量。
    """
    reasons = " ".join(str(item.get("reason") or "") for item in rounds).lower()
    failed = list(gate.get("failed_checks", [])) if gate else []
    if any(token in reasons for token in ("pdu", "power", "bench", "插座")):
        return {"layer": "bench", "basis": "轮次原因包含 PDU/台架失败", "suspects": []}
    if any(token in reasons for token in ("serial", "com", "recording", "record_open", "capture", "no_data")):
        return {"layer": "device", "basis": "轮次原因包含串口/录音/采集失败", "suspects": []}
    if gate and failed and all(item in ARTIFACT_GATE_CHECKS for item in failed):
        return {
            "layer": "tool",
            "basis": "唯一未通过的 gate 项是交付件检查: " + ", ".join(failed),
            "suspects": [],
        }
    suspects: list[str] = []
    for item in anomalies:
        if item["code"] in ATTEMPT_STATUSES:
            suspects.append(f"{item['code']}：首轮尝试未成功，需隔离播报间隔/语料/设备唤醒延迟")
    if suspects:
        return {"layer": "unknown", "basis": "无变量隔离证据，不能归因到单一层", "suspects": suspects}
    if gate and not gate.get("gate_pass"):
        return {"layer": "tool", "basis": "gate 未通过: " + ", ".join(failed), "suspects": []}
    return {"layer": "unknown", "basis": "未发现失败项或归因证据", "suspects": []}


def _test_outcome(rounds: Sequence[dict[str, Any]], target: Any, end: EventRecord | None) -> tuple[str, str]:
    if not rounds:
        return "UNKNOWN", "没有 ROUND_RESULT 事件"
    failed = [item for item in rounds if item["status"] in STATUS_FAILURE]
    blocked = [item for item in rounds if item["status"] == "BLOCKED"]
    passes = [item for item in rounds if item["status"] == "PASS"]
    if blocked:
        return "BLOCKED", f"{len(blocked)} 轮 BLOCKED"
    if failed:
        return "FAIL", f"{len(failed)} 轮未通过"
    if end is None:
        return "INCOMPLETE", "没有 TASK_END，任务被中断或未收尾"
    if target and len(passes) < int(target):
        return "INCOMPLETE", f"完成 {len(passes)}/{int(target)} 轮"
    return "PASS", f"{len(passes)} 轮全部 PASS"


def _delivery(gate: Mapping[str, Any] | None, outcome: str) -> tuple[str, list[str]]:
    if gate is None:
        return "UNKNOWN", []
    failed = list(gate.get("failed_checks", []))
    if gate.get("gate_pass") is True:
        return "COMPLETE", []
    if not failed:
        return "UNKNOWN", []
    if all(item in ARTIFACT_GATE_CHECKS for item in failed):
        return "DELIVERY_INCOMPLETE", failed
    if all(item in BLOCKING_GATE_CHECKS for item in failed):
        return "BLOCKED", failed
    if outcome == "FAIL":
        return "DELIVERY_INCOMPLETE", failed
    return "DELIVERY_INCOMPLETE", failed


def _final_status(outcome: str, delivery: str) -> str:
    if outcome == "FAIL":
        return "FAIL"
    if outcome == "BLOCKED" or delivery == "BLOCKED":
        return "BLOCKED"
    if outcome in {"INCOMPLETE", "UNKNOWN"} or delivery != "COMPLETE":
        return "NEEDS_REVIEW"
    return "PASS"


def _next_steps(outcome: str, delivery: str, gate: Mapping[str, Any] | None,
                rounds: Sequence[dict[str, Any]], target: Any,
                rates: Mapping[str, Any]) -> list[str]:
    steps: list[str] = []
    if gate and not gate.get("gate_pass"):
        failed = ", ".join(gate.get("failed_checks") or []) or "gate 未通过"
        steps.append(f"补齐 gate 未通过项后重新评估：{failed}")
    if rates.get("first_attempt_success_rate") is not None and rates.get("round_success_rate") is not None:
        if rates["first_attempt_success_rate"] + 0.1 < rates["round_success_rate"]:
            steps.append(
                "隔离首轮未唤醒：固定语料与设备，只改变播报间隔/重播策略做对照，"
                "再判定是否可接受"
            )
    if outcome == "INCOMPLETE" and target:
        steps.append(f"补跑至目标轮次 {int(target)} 或说明停止原因")
    if outcome == "UNKNOWN":
        steps.append("确认 ROUND_RESULT 是否缺失；仅有广播事件不能出具轮次结论")
    missing = [item for item in rounds if item.get("first_success_attempt") is None and item.get("attempts_total") is None]
    if missing:
        steps.append(f"{len(missing)} 轮缺少 attempt 统计，需在工具侧补齐")
    if not steps:
        steps.append("按现有结论签发；如需对外发布，再补人工签字与归档")
    return steps[:5]


def _environment(records: Sequence[EventRecord], meta: Mapping[str, Any]) -> dict[str, Any]:
    """环境/工具/输入来自事件里的任务上下文，缺失即为 null，不猜。"""
    start = next((record for record in records if record.event in {"TASK_START", "TASK_STARTED"}), None)
    details = start.details if start is not None and isinstance(start.details, Mapping) else {}
    promoted = details.get("environment") if isinstance(details.get("environment"), Mapping) else {}
    raw_probe: Mapping[str, Any] = {}
    for record in records:
        if record.event == "TASK_START" and record.raw_ref:
            ref_path = Path(record.raw_ref.get("path") or "")
            if ref_path:
                raw_probe = {"raw_ref": record.raw_ref}
            break
    alias = {
        "node": ("node", "host", "hostname"),
        "os": ("platform", "os", "os_version"),
        "python": ("python", "python_version"),
        "runtime": ("runtime", "execution"),
        "cwd": ("cwd", "workdir"),
    }
    meta_environment = meta.get("environment") if isinstance(meta.get("environment"), Mapping) else {}
    environment: dict[str, Any] = {}
    for key, names in alias.items():
        found = _pick(promoted, names) or _pick(details, names) or _pick(meta_environment, names)
        environment[key] = sanitize_path(found) if key == "cwd" else (None if _skip(found) else str(found))
    if raw_probe:
        environment["raw_ref"] = raw_probe["raw_ref"]

    tooling = {
        "tool": _pick(details, ("tool", "tool_name", "script")) or _pick(meta, ("tool",)),
        "source": start.source if start is not None else None,
        "profile_id": _pick(details, ("profile_id",)) or _pick(meta, ("profile_id",)),
        "profile_version": _pick(details, ("profile_version",)) or _pick(meta, ("profile_version",)),
        "contract": _pick(details, ("lstest_contract", "contract")) or _pick(meta, ("contract",)),
        "contract_files_sha256": _pick(details, ("lstest_ref_sha256", "contract_files_sha256")),
    }
    for key in ("tool", "source", "profile_id", "profile_version", "contract"):
        if _skip(tooling.get(key)):
            tooling[key] = None
    if not isinstance(tooling.get("contract_files_sha256"), Mapping):
        tooling["contract_files_sha256"] = None

    inputs: list[dict[str, Any]] = []
    explicit = details.get("inputs")
    if isinstance(explicit, list):
        for item in explicit:
            if isinstance(item, Mapping):
                inputs.append({
                    "path": sanitize_path(item.get("path") or item.get("file")),
                    "sha256": item.get("sha256"),
                    "format": item.get("format"),
                })
    else:
        for path_key, sha_key, format_key in (
            ("wake_wav", "wake_wav_sha256", "wake_wav_format"),
            ("audio_file", "audio_sha256", "audio_format"),
            ("input", "input_sha256", "input_format"),
        ):
            path = details.get(path_key)
            if _skip(path):
                continue
            inputs.append({
                "path": sanitize_path(path),
                "sha256": details.get(sha_key),
                "format": details.get(format_key),
            })
    meta_inputs = meta.get("inputs") if isinstance(meta.get("inputs"), list) else []
    seen_inputs = {(item.get("path"), item.get("sha256")) for item in inputs}
    for item in meta_inputs:
        if isinstance(item, Mapping):
            entry = {
                "path": sanitize_path(item.get("path")),
                "sha256": item.get("sha256"),
                "format": item.get("format"),
            }
            if (entry["path"], entry["sha256"]) not in seen_inputs:
                inputs.append(entry)
                seen_inputs.add((entry["path"], entry["sha256"]))

    parameters = {}
    gap_policy = _pick(details, ("gap_policy",))
    if not _skip(gap_policy):
        parameters["gap_policy"] = str(gap_policy)
    if _pick(details, ("task_dir", "result_dir")):
        parameters["task_dir"] = sanitize_path(_pick(details, ("task_dir",)))
        parameters["result_dir"] = sanitize_path(_pick(details, ("result_dir",)))
    return {
        "environment": environment,
        "tooling": tooling,
        "inputs": inputs,
        "parameters": parameters,
    }


def _pick(container: Mapping[str, Any] | None, names: Iterable[str]) -> Any:
    if not isinstance(container, Mapping):
        return None
    for name in names:
        value = container.get(name)
        if not _skip(value):
            return value
    return None


def _evidence_index(records: Sequence[EventRecord]) -> list[dict[str, Any]]:
    purpose = (
        ("logs/serial/", "设备串口原始证据"),
        ("serial_logs/", "设备串口原始证据"),
        ("execution_logs/", "执行日志"),
        ("evidence/raw/", "外置 raw 证据"),
        ("raw/", "外置 raw 证据"),
        ("reports/", "机器报告"),
        ("recoder/", "本轮录音"),
        ("recorder/", "本轮录音"),
        (".pcm", "本轮录音"),
        (".wav", "音频素材"),
        ("results.csv", "逐轮结果表"),
        ("cases.csv", "用例冻结表"),
        ("milestones", "里程碑"),
        ("tool.log", "旧版人读账本"),
    )
    grouped: dict[str, dict[str, Any]] = {}
    for record in records:
        for ref in record.evidence or []:
            path = ref.get("path")
            if not path:
                continue
            name = next((label for token, label in purpose if token in path), "其他证据")
            entry = grouped.setdefault(path, {
                "path": path,
                "purpose": name,
                "lines": [],
                "sha256": ref.get("sha256"),
                "exists": None,
            })
            if ref.get("line") is not None and ref["line"] not in entry["lines"]:
                entry["lines"].append(ref["line"])
            if ref.get("sha256") and not entry["sha256"]:
                entry["sha256"] = ref["sha256"]
    index = []
    for entry in grouped.values():
        entry["lines"].sort()
        entry["first_event_seq"] = None
        index.append(entry)
    for entry in index:
        for record in records:
            if any(ref.get("path") == entry["path"] for ref in record.evidence or []):
                entry["first_event_seq"] = record.event_seq
                break
    return sorted(index, key=lambda item: (item["first_event_seq"] or 0, item["path"]))


def summarize(records: Sequence[EventRecord], meta: Mapping[str, Any] | None = None,
              extension: "state_catalog.StateExtension | None" = None) -> dict[str, Any]:
    """从事件确定性生成 summary.json 内容。除 ``meta``/状态扩展外不读任何外部状态。"""
    records = list(records)
    meta = dict(meta or {})
    start = next((record for record in records if record.event in {"TASK_START", "TASK_STARTED"}), None)
    end = _end_record(records)
    rounds = _rounds(records)
    target = _target_rounds(records)
    milestone = _milestone(records)
    gate = _gate(records)
    state_report = state_catalog.analyze(records, extension)
    # 状态 gate 只在 profile 声明了必需状态/容忍度时生效；未声明时保持旧行为。
    if state_report["gate_checks"]:
        checks = dict((gate or {}).get("checks") or {})
        checks.update(state_report["gate_checks"])
        merged = dict(gate or {})
        merged["checks"] = dict(sorted(checks.items()))
        merged["failed_checks"] = sorted(key for key, value in checks.items() if value is False)
        merged["gate_pass"] = not merged["failed_checks"]
        merged.setdefault("source_event", "STATE_CATALOG")
        gate = merged
    anomalies = _anomalies(records, rounds)
    latency = _latency(records, rounds)
    outcome, outcome_basis = _test_outcome(rounds, target, end)
    delivery, delivery_failed = _delivery(gate, outcome)
    final = _final_status(outcome, delivery)

    passes = sum(1 for item in rounds if item["status"] == "PASS")
    fails = sum(1 for item in rounds if item["status"] in (STATUS_FAILURE - {"BLOCKED", "TIMEOUT"}))
    blocked = sum(1 for item in rounds if item["status"] in {"BLOCKED", "TIMEOUT"})
    other = len(rounds) - passes - fails - blocked
    total = len(rounds) or 1
    first_attempt = sum(1 for item in rounds if item.get("first_success_attempt") == 1)
    attempts_total = sum(item.get("attempts_total") or 0 for item in rounds)
    max_attempts = max((item.get("attempts_total") or 0) for item in rounds) if rounds else 0
    distribution = _distribution(rounds, max_attempts)
    round_rate = round(passes * 100.0 / total, 1) if rounds else None
    first_rate = round(first_attempt * 100.0 / total, 1) if rounds else None
    rates = {
        "round_success_rate": round_rate,
        "first_attempt_success_rate": first_rate,
        "first_attempt_gap_pct": (
            round(round_rate - first_rate, 1)
            if rounds and round_rate is not None and first_rate is not None else None
        ),
        "rounds_total": len(rounds),
        "rounds_target": target,
        "rounds_missing": (int(target) - len(rounds)) if target else None,
        "attempts_total": attempts_total,
        "avg_attempts": round(attempts_total / len(rounds), 2) if rounds else None,
        "attempt_distribution": distribution,
    }

    conflicts: list[dict[str, Any]] = []
    if end is not None and gate is not None:
        recorded = (end.status or "").upper()
        if recorded == "PASS" and gate.get("gate_pass") is False:
            conflicts.append({
                "code": "TASK_END_STATUS_CONFLICT",
                "detail": "TASK_END.status=PASS 与 gate.gate_pass=false 冲突",
                "resolved_as": final,
                "source_event_seq": end.event_seq,
            })
        if recorded in {"FAIL", "BLOCKED"} and gate.get("gate_pass") is True:
            conflicts.append({
                "code": "TASK_END_STATUS_CONFLICT",
                "detail": f"TASK_END.status={recorded} 与 gate.gate_pass=true 冲突",
                "resolved_as": final,
                "source_event_seq": end.event_seq,
            })
    for item in anomalies:
        if item["code"] == "STATUS_CONFLICT":
            conflicts.append({
                "code": "EVENT_STATUS_CONFLICT",
                "detail": item.get("example") or "事件内 status 冲突",
                "resolved_as": final,
                "source_event_seq": item.get("first_event_seq"),
            })

    missing: list[str] = []
    if gate is None:
        missing.append("TASK_END.gate 缺失，无法签发")
    missing.extend(f"gate 未通过: {item}" for item in delivery_failed if item not in missing)
    if end is None:
        missing.append("TASK_END 缺失")
    if target and len(rounds) < int(target):
        missing.append(f"目标 {int(target)} 轮只完成 {len(rounds)} 轮")
    for item in rounds:
        if item.get("attempts_total") is None:
            missing.append(f"R{item['round']} 缺少 attempt 统计")
    for item in rounds:
        if item.get("first_success_attempt") is None and item.get("attempts_total"):
            missing.append(f"R{item['round']} 缺少 first_success_attempt")
    missing = list(dict.fromkeys(missing))

    task_id = next((record.task_id for record in records if record.task_id), None)
    meta_block = {
        "generated_at": now_human(),
        "generator": "lstest/scripts/eventlog.py",
        "bundle_version": BUNDLE_VERSION,
        "display_format": "unified",
        "task_id": task_id,
        "title": meta.get("title") or (start.message if start else None) or task_id,
        "purpose": meta.get("purpose"),
        "scope": meta.get("scope"),
        "mode": _pick(start.expected if start else None, ("mode",)) or _pick(meta, ("mode",)),
        "events_path": "events.jsonl",
        "events_count": len(records),
        "events_sha256": meta.get("events_sha256"),
        "source_log": meta.get("import") if isinstance(meta.get("import"), Mapping) else None,
    }
    context = _environment(records, meta)
    if meta.get("title"):
        meta_block["title"] = meta["title"]
    if meta.get("purpose"):
        meta_block["purpose"] = meta["purpose"]

    first_failure = _first_failure(rounds)
    worst = _worst_round(rounds)
    responsibility = _responsibility(rounds, gate, anomalies)
    if first_failure:
        responsibility = {
            "layer": responsibility["layer"],
            "basis": "首败轮次原因: " + str(first_failure.get("reason") or "未记录"),
            "suspects": responsibility.get("suspects", []),
        }

    summary = {
        "schema": SUMMARY_SCHEMA,
        "bundle_version": BUNDLE_VERSION,        "meta": meta_block,
        "task_id": task_id,
        "test_outcome": outcome,
        "test_outcome_basis": outcome_basis,
        "delivery_status": delivery,
        "delivery_failed_checks": delivery_failed,
        "final_status": final,
        "final_status_rule": _final_rule_text(outcome, delivery),
        "counts": {
            "rounds_total": len(rounds),
            "pass": passes,
            "fail": fails,
            "blocked": blocked,
            "other": other,
        },
        "rates": rates,
        "latency": latency,
        "milestone": milestone,
        "anomalies": anomalies,
        "conflicts": conflicts,
        "first_failure": first_failure,
        "worst_round": worst,
        "responsibility": responsibility,
        "gate": gate,
        "state_report": state_report,
        "missing": missing,
        "next_steps": _next_steps(outcome, delivery, gate, rounds, target, rates),
        **context,
        "evidence_index": _evidence_index(records),
    }
    summary["_primary_device"] = next(
        (record.device for record in records if record.event == "DEVICE_BINDING" and record.device), None
    )
    summary["_rounds"] = rounds
    return summary


def _final_rule_text(outcome: str, delivery: str) -> str:
    if outcome == "PASS" and delivery == "COMPLETE":
        return "测试通过且交付完整，允许签发 PASS"
    if outcome == "PASS":
        return f"测试结果 PASS，但交付状态 {delivery}，不得签发 PASS"
    return f"测试结果 {outcome}，交付状态 {delivery}"


def public_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    """去掉渲染用的内部字段，得到落盘的 summary.json 内容。"""
    return {key: value for key, value in summary.items() if not key.startswith("_")}


# ---------------------------------------------------------------------------
# tool.log：完整动作审计（TOOL + DEVICE 单行时序）
# ---------------------------------------------------------------------------

TOOL_LOG_KINDS = {
    "START", "END", "STATE", "JUDGE", "WAIT", "RETRY", "TIMEOUT", "PASS", "FAIL",
    "BLOCKED", "NO_WAKE", "WARN", "ERROR", "SKIP", "CANCEL", "NEEDS_REVIEW",
    "MILESTONE", "EVENT",
}
TOOL_LOG_LINE_RE = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) "
    r"\[(?P<scope>[^\]\n]+)\]\[(?P<category>[A-Z_]+)\]"
    r"(?:\[(?P<kind>[A-Z_]+)\])?(?:\[(?P<result>[A-Z_]+)\])? (?P<body>.+)$"
)
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
AUDIT_CATEGORY_RULES = (
    ("ENV", ("DEVICE_BINDING", "TASK_CONFIG", "ENVIRONMENT")),
    ("RESULT", ("ROUND_RESULT", "CASE_RESULT")),
    ("RUN", ("TASK_", "MILESTONE")),
    ("PDU", ("PDU_", "POWER_")),
    ("WAKE", ("WAKE", "BROADCAST", "PRE_WAKEUP")),
    ("REC", ("RECORD",)),
    ("PLAYER", ("PLAYER_", "PLAY_", "HOST_AUDIO")),
    ("SERIAL", ("SERIAL_",)),
    ("TTS", ("TTS_",)),
    ("ASR", ("ASR_", "RECOGNITION")),
    ("NET", ("WIFI_", "CLOUD_", "ONLINE_", "PROTOCOL_", "HEARTBEAT")),
    ("DEVICE", ("DEVICE_", "BOOT_", "READY", "REBOOT", "RESET_", "CRASH", "WATCHDOG")),
)
SCALAR_LIMIT = 120
DEVICE_TS_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\]")

#: tool.log 的短字段名（只压缩框架自己的字段名，取值不变）。
FACT_LABELS = {
    "attempts_total": "attempts",
    "first_success_attempt": "first_ok",
    "first_play_start_offset_s": "play_s",
    "recording_start_offset_s": "rec_start_s",
    "first_wakeup_offset_s": "wake_s",
    "play_start_offset_s": "play_s",
    "play_end_offset_s": "play_end_s",
    "response_start_offset_s": "resp_start_s",
    "response_complete_offset_s": "resp_end_s",
    "next_gap_s": "gap_s",
    "wake_check_window_s": "window_s",
    "power_on_at": "power_on",
    "format": "fmt",
    "channels": "ch",
    "avg_sec_per_round": "avg_s_per_round",
    "recent_10_elapsed_sec": "recent10_s",
    "success_rate": "success_rate",
}


def ensure_ms(value: Any) -> str | None:
    """任何日志时间统一成北京时间毫秒：`YYYY-MM-DD HH:MM:SS.mmm`。

    接受已有毫秒、只到秒、`T` 分隔、尾部 `Z` 或时区偏移；不合法时原样返回（由 verify 报错）。
    """
    if _skip(value):
        return None
    text = str(value).strip().replace("T", " ")
    match = re.match(
        r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(?:Z|[+-]\d{2}:?\d{2})?$",
        text,
    )
    if not match:
        return text
    fraction = (match.group(2) or "000")[:3].ljust(3, "0")
    return f"{match.group(1)}.{fraction}"


def one_line(value: Any) -> str:
    """把任意文本压成物理一行：换行/控制字符转义，绝不产生续行。"""
    if _skip(value):
        return ""
    text = str(value)
    text = text.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")
    text = CONTROL_CHARS.sub(" ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _clip(value: Any, limit: int = SCALAR_LIMIT) -> str:
    text = _text(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _short_hash(value: Any) -> str:
    text = str(value or "")
    return text if len(text) <= 16 else text[:16] + "…"


def _audit_scope(record: EventRecord) -> str:
    if record.event.startswith("TASK_") or record.round is None:
        return "TASK"
    scope = f"R{int(record.round):02d}"
    if record.attempt is not None:
        scope += f"/A{int(record.attempt):02d}"
    return scope


def _audit_category(record: EventRecord) -> str:
    if isinstance(record.details, Mapping) and record.details.get("device"):
        return "DEVICE"
    if isinstance(record.details, Mapping) and record.details.get("audit_category"):
        return str(record.details["audit_category"]).upper()
    event = record.event.upper()
    for label, tokens in AUDIT_CATEGORY_RULES:
        if any(token in event for token in tokens):
            return label
    return "DEVICE" if record.source in {"device", "DEVICE"} else "TOOL"


def _audit_kind(record: EventRecord) -> str:
    if isinstance(record.details, Mapping) and record.details.get("device"):
        # 设备事件第三段就是设备状态码（BOOT_START/READY/WAKEUP/ASR_RESULT/TTS_END…）。
        code = record.event.upper().removeprefix("DEVICE_") or "UNPARSED"
        return code
    if isinstance(record.details, Mapping) and record.details.get("audit_kind"):
        return str(record.details["audit_kind"]).upper()
    event = record.event
    if event in {"TASK_START", "TASK_STARTED"}:
        return "START"
    if event in {"TASK_END", "TASK_FINISHED"}:
        return "END"
    if event == "MILESTONE":
        return "MILESTONE"
    if event in {"ROUND_RESULT", "CASE_RESULT"}:
        return "JUDGE"
    status = (record.status or "").upper().replace("NO_WAKEUP", "NO_WAKE")
    state_events = {"DEVICE_BINDING", "SERIAL_OPEN", "SERIAL_CLOSE", "POWER_OFF", "POWER_ON"}
    if status:
        if status in {"PASS", "OK", "OBSERVED"} and (
            event in state_events or (not record.expected and not record.actual)
        ):
            return "STATE"
        return status if status in TOOL_LOG_KINDS else "JUDGE"
    if event.startswith(("SERIAL_", "PDU_", "POWER_", "PLAYER_", "RECORD", "BOOT_")):
        return "STATE"
    return "EVENT"


def _compact(value: Any, limit: int = 200) -> str:
    """把 expected/actual 压成单行 key=value；raw 不进入此函数。"""
    if _skip(value):
        return ""
    if isinstance(value, Mapping):
        parts: list[str] = []
        for key, item in value.items():
            label = FACT_LABELS.get(str(key), str(key))
            if isinstance(item, Mapping):
                nested = [
                    f"{sub}={_clip(sub_item, 60)}"
                    for sub, sub_item in item.items()
                    if not isinstance(sub_item, (Mapping, list, tuple)) and not _skip(sub_item)
                ]
                parts.append(f"{label}=" + ("{" + ", ".join(nested) + "}" if nested else f"<{len(item)} keys>"))
            elif isinstance(item, (list, tuple)):
                if all(isinstance(entry, (str, int, float, bool)) for entry in item):
                    parts.append(f"{label}=" + "|".join(_clip(entry, 60) for entry in item))
                else:
                    parts.append(f"{label}=<{len(item)} items>")
            else:
                rendered = _short_hash(item) if "sha256" in str(key) else _clip(item, 80)
                parts.append(f"{label}={rendered}")
        text = ", ".join(parts)
    elif isinstance(value, (list, tuple)):
        text = "|".join(_clip(entry, 60) for entry in value)
    else:
        text = _clip(value, limit)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _audit_hits(record: EventRecord) -> str:
    actual = record.actual if isinstance(record.actual, Mapping) else {}
    hits = [str(key) for key, value in actual.items() if value is True]
    if hits:
        return ",".join(hits)
    if actual:
        return "none"
    return ""


EVENT_MESSAGES = {
    "TASK_START": "任务开始",
    "TASK_STARTED": "任务开始",
    "TASK_END": "任务结束",
    "TASK_FINISHED": "任务结束",
    "DEVICE_BINDING": "绑定设备与音频端点",
    "MILESTONE": "里程碑",
    "SERIAL_OPEN": "打开串口",
    "SERIAL_CLOSE": "关闭串口",
    "PDU_CYCLE": "PDU 断电上电",
    "RECORDING": "本轮录音开启并结束",
    "ROUND_RESULT": "本轮判定",
    "CASE_RESULT": "用例判定",
}


def _audit_next(record: EventRecord) -> str:
    handling = (record.handling or "").lower()
    mapping = {
        "continue": "RETRY",
        "retry": "RETRY",
        "stop_round": "NEXT_ROUND",
        "stop_task": "STOP",
        "abort": "STOP",
        "blocked": "BLOCKED",
    }
    if handling in mapping:
        return mapping[handling]
    return handling.upper() if handling else ""


def _audit_evidence(record: EventRecord) -> str:
    refs = []
    for ref in record.evidence or []:
        path = ref.get("path") or ""
        if not path:
            continue
        refs.append(f"{path}#{ref['line']}" if ref.get("line") is not None else path)
    return ",".join(refs[:4])


def _audit_facts(record: EventRecord, summary: Mapping[str, Any], *, since_prev: float | None,
                 unified: bool = False) -> list[str]:
    """按“做法/状态/判断/产物/证据”顺序组装单行事实，缺失项不输出。"""
    facts: list[str] = []
    seen: dict[str, str] = {}

    def add(key: str, value: Any, limit: int = SCALAR_LIMIT) -> None:
        if _skip(value) or key in seen:
            return
        text = _clip(value, limit)
        if not text or text == "-":
            return
        seen[key] = text
        facts.append(f"{key}={text}")

    details = record.details if isinstance(record.details, Mapping) else {}
    is_device = bool(details.get("device"))
    if record.event in {"TASK_START", "TASK_STARTED"}:
        # 任务开头一行说清“测什么/用什么/输入是什么”，不展开工具自定义的大对象。
        expected_map = record.expected if isinstance(record.expected, Mapping) else {}
        for key in ("runtime", "profile_id", "profile_version", "gap_policy", "mode", "target_rounds"):
            add(key, details.get(key) if not _skip(details.get(key)) else expected_map.get(key), 60)
        for path_key, sha_key in (
            ("wake_wav", "wake_wav_sha256"), ("audio_file", "audio_sha256"), ("input", "input_sha256"),
        ):
            path = details.get(path_key)
            if _skip(path):
                continue
            digest = details.get(sha_key) or ""
            add("input", f"{sanitize_path(path)}#{_short_hash(digest)}" if digest else sanitize_path(path), 80)
        spec = _compact(record.expected)
        if spec:
            add("spec", spec, 160)
        return facts
    if not is_device:
        source = record.source or (summary.get("tooling") or {}).get("tool")
        if source:
            add("tool", source)
    if record.phase:
        add("phase", record.phase)
    if record.case_id:
        add("case", record.case_id)
    if record.action_id:
        add("action", record.action_id)
    if record.broadcast_id and record.broadcast_id != record.action_id:
        add("broadcast", record.broadcast_id)
    if is_device and record.correlation_id and not unified:
        add("corr", record.correlation_id, 60)

    parameters = details.get("parameters")
    if isinstance(parameters, Mapping):
        for key, value in parameters.items():
            add(str(key), value, 80)
    internal = {
        "device", "audit_category", "audit_kind", "marker", "repeat_count", "last_hit",
        "source_event", "source_event_seq", "unparsed", "legacy_fields", "parameters",
        "raw_excerpt", "raw_sha256", "environment", "lstest_ref_sha256", "audit_before",
        "audit_after", "state", "catalog_status", "raw_code", "display", "raw_marker",
        "rule_id",
    }
    for key, value in details.items():
        if str(key) in internal or str(key).startswith("audit_"):
            continue
        if str(key).endswith(("_format", "_ref_sha256")):
            continue
        if isinstance(value, Mapping):
            for sub, item in value.items():
                if _skip(item) or isinstance(item, Mapping):
                    continue
                add(f"{key}.{sub}", item, 60)
        elif str(key).endswith("_sha256") or str(key) == "sha256":
            add(str(key), _short_hash(value), 24)
        else:
            add(str(key), value, 80)
    if details.get("state"):
        add("state", details["state"], 40)
    retries = _num(details.get("open_attempts")) or _num(
        record.actual.get("open_attempts") if isinstance(record.actual, Mapping) else None
    )
    if retries and retries > 1:
        add("retries", int(retries) - 1)
    repeat_count = _num(details.get("repeat_count"))
    if repeat_count and repeat_count > 1:
        add("repeat", repeat_count)
        add("last_hit", details.get("last_hit"), 32)
    for label in ("audit_before", "audit_after"):
        if details.get(label):
            add(label.replace("audit_", "state_"), details[label], 80)
    if record.max_attempts is not None:
        add("max_attempts", record.max_attempts)
    if isinstance(record.normalized, Mapping):
        for key, value in record.normalized.items():
            if not isinstance(value, Mapping):
                add(FACT_LABELS.get(str(key), str(key)), value, 40)

    expected = _compact(record.expected)
    actual = _compact(record.actual)
    status_upper = str(record.status or "").upper()
    if expected and expected == actual:
        add("spec", expected, 240)
    else:
        if expected:
            add("expected", expected, 240)
        if actual:
            add("actual", actual, 240)
    judging = record.event in {"ROUND_RESULT", "CASE_RESULT", "BROADCAST"} or (
        status_upper in {"FAIL", "BLOCKED", "NO_WAKE", "NEEDS_REVIEW"} and bool(expected or actual)
    )
    if judging:
        hits = _audit_hits(record)
        if hits:
            add("hit", hits, 80)
    if record.rule_id and not unified:
        add("rule", record.rule_id, 60)
    if record.reason:
        add("reason", record.reason, 80)
    kind = _audit_kind(record)
    if status_upper and status_upper != kind and (
        kind in {"JUDGE", "END"} or status_upper not in {"PASS", "OK", "OBSERVED"}
    ):
        add("result", record.status, 24)
    next_step = _audit_next(record)
    if next_step:
        add("next", next_step)
    if record.elapsed_ms is not None:
        add("elapsed", f"{_text(record.elapsed_ms)}ms")
    if since_prev is not None and since_prev >= 1.0:
        add("since_prev", f"{since_prev:.3f}s")
    evidence = _audit_evidence(record)
    if evidence:
        add("evidence", evidence, 160)
    elif record.raw_ref and record.raw_ref.get("path"):
        add("evidence", record.raw_ref["path"], 80)
    return facts


def _device_audit_line(record: EventRecord, scope: str, stamp: str) -> str:
    """统一设备行：`[范围][DEVICE] 类别：业务值`。

    只显示归一化状态类别和业务值；不显示 raw_marker、正则名、rule id、捕获字段名、
    内部解析 ID、device_ts 或 catalog_status。raw 原文与证据留在 events.jsonl/raw。
    """
    details = record.details if isinstance(record.details, Mapping) else {}
    if details.get("unparsed"):
        excerpt = _clip(one_line(details.get("raw_excerpt") or record.message or ""), 120)
        return f"{stamp} [{scope}][DEVICE] 未识别设备状态：{excerpt}".rstrip()
    code = record.event.upper()
    state, _status = (state_catalog.CATALOG.get(code), "")
    if state is None:
        state = state_catalog.CATALOG[state_catalog.UNPARSED_CODE]
    parameters = details.get("parameters")
    display = details.get("display") if isinstance(details.get("display"), Mapping) else None
    described = state_catalog.describe_device(code, parameters, display)
    category = described["category"]
    primary = described["primary"]
    stage = described["stage"]
    fields = described["fields"]
    parts = []
    if primary:
        body = f"{category}：{primary}"
        if fields:
            parts = [f"{label}:{value}" if label in {"zh", "中文"} else f"{label}={value}"
                     for label, value in fields]
    elif fields:
        body = f"{category}："
        parts = [f"{label}:{value}" if label in {"zh", "中文"} else f"{label}={value}"
                 for label, value in fields]
    elif stage:
        body = f"{category}：{stage}"
    else:
        label = one_line(str((display or {}).get("label") or state.label_zh or category))
        body = f"{category}：{label}"
    if parts:
        body += "，".join(parts)
    if stage and (primary or fields):
        body += f"，{stage}"
    return f"{stamp} [{scope}][DEVICE] {body}".rstrip()


def _audit_line(record: EventRecord, summary: Mapping[str, Any], *, since_prev: float | None = None,
                display_format: str = "unified") -> str:
    scope = _audit_scope(record)
    category = _audit_category(record)
    kind = _audit_kind(record)
    status = (record.status or "").upper().replace("NO_WAKEUP", "NO_WAKE")
    stamp = ensure_ms(record.time) or "1970-01-01 00:00:00.000"
    details = record.details if isinstance(record.details, Mapping) else {}
    if display_format == "unified" and details.get("device"):
        return _device_audit_line(record, scope, stamp)

    if record.event in {"TASK_END", "TASK_FINISHED"}:
        # 收尾行必须同时给出动作结果、测试结果、交付状态与最终判定，避免再次出现
        # “记录 PASS 但 gate 未通过”的矛盾记录。
        final = str(summary.get("final_status") or "UNKNOWN")
        rates = summary.get("rates") or {}
        counts = summary.get("counts") or {}
        gate = summary.get("gate") or {}
        gate_state = "UNKNOWN" if gate.get("gate_pass") is None else (
            "PASS" if gate.get("gate_pass") else "FAIL(" + ",".join(gate.get("failed_checks") or []) + ")"
        )
        parts = [
            "任务结束",
            f"recorded={status or '-'}",
            f"test_outcome={summary.get('test_outcome')}",
            f"delivery={summary.get('delivery_status')}",
            f"final={final}",
            f"gate={gate_state}",
            f"rounds={counts.get('rounds_total', 0)}/{rates.get('rounds_target') or '?'}",
        ]
        if record.reason:
            parts.append(f"reason={record.reason}")
        if record.elapsed_ms is not None:
            parts.append(f"elapsed={_text(record.elapsed_ms)}ms")
        evidence = _audit_evidence(record)
        if evidence:
            parts.append(f"evidence={evidence}")
        return f"{stamp} [{scope}][{category}][END][{final}] " + " | ".join(parts)

    tokens = f"[{scope}][{category}][{kind}]"
    if status and status != kind and kind in {"JUDGE", "END", "EVENT"}:
        tokens += f"[{status}]"
    message = one_line(record.message) or EVENT_MESSAGES.get(record.event) or one_line(record.event)
    facts = _audit_facts(record, summary, since_prev=since_prev,
                         unified=display_format == "unified")
    body = message
    if facts:
        body += (" | " if body else "") + " | ".join(facts)
    return f"{stamp} {tokens} {body}".rstrip()


def validate_tool_log_text(text: str) -> list[str]:
    """校验 tool.log 单行时序：每行必须毫秒时间戳 + 三段标签，不得有续行。"""
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            errors.append(f"第 {number} 行是空行；tool.log 不允许空行")
            continue
        match = TOOL_LOG_LINE_RE.match(line)
        if not match:
            errors.append(f"第 {number} 行不是单行时序格式（需 ms 时间戳 + [范围][动作][阶段]）")
            continue
        kind = match.group("kind") or ""
        body = match.group("body")
        is_device = match.group("category") == "DEVICE"
        if is_device:
            # 新格式：`[scope][DEVICE] 类别：业务值`；旧格式 `[DEVICE][CODE]` 仍可读。
            if kind:
                if not re.fullmatch(r"[A-Z][A-Z0-9_]*", kind):
                    errors.append(f"第 {number} 行设备状态码不合法: {kind}")
            else:
                for token in ("raw_marker=", "rule=", "rule_id=", "catalog_status=",
                              "raw_code=", "correlation_id=", "device_ts=", "event_seq="):
                    if token in body:
                        errors.append(f"第 {number} 行设备行泄露内部信息: {token}")
                if "：" not in body:
                    errors.append(f"第 {number} 行设备行缺少“类别：业务值”结构")
        elif kind not in TOOL_LOG_KINDS:
            errors.append(f"第 {number} 行使用未注册阶段标签 {kind or '<缺失>'}")
    return errors


def render_tool_log(summary: Mapping[str, Any], records: Sequence[EventRecord],
                    display_format: str | None = None) -> str:
    """把事件渲染成完整动作审计：每个事件一行，按 host 毫秒排序。

    规则：
    - TOOL 与 DEVICE 事件同一时序；不写续行、不写空行、不写汇总段落。
    - 每行自足：范围/动作/阶段 + 做法 + 前后状态 + 判断依据 + 证据。
    - 连续重复的 DEVICE 状态合并为一行并给出 repeat_count 与 last_hit。
    - ``display_format="unified"`` 时设备行只显示统一类别与业务值（默认）；
      旧摘要（meta 无 ``display_format``）按 legacy 渲染，保证旧目录仍可 verify。
    """
    if display_format is None:
        display_format = (summary.get("meta") or {}).get("display_format") or "legacy"
        display_format = "unified" if str(display_format).startswith("unified") else "legacy"
    ordered = sorted(
        enumerate(records),
        key=lambda pair: (ensure_ms(pair[1].time) or "9999-12-31 23:59:59.999", pair[0]),
    )
    lines: list[str] = []
    last_scope: str | None = None
    last_time: str | None = None
    pending: dict[str, Any] | None = None
    for _, record in ordered:
        scope = _audit_scope(record)
        stamp = ensure_ms(record.time)
        since_prev = None
        if stamp and last_time and scope == last_scope:
            try:
                delta = (datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S.%f")
                         - datetime.strptime(last_time, "%Y-%m-%d %H:%M:%S.%f")).total_seconds()
                since_prev = max(0.0, round(delta, 3))
            except ValueError:
                since_prev = None
        signature = (
            scope, _audit_category(record), _audit_kind(record),
            record.event, _compact(record.actual, 160), _compact(record.details, 160),
        )
        is_device = isinstance(record.details, Mapping) and bool(record.details.get("device"))
        if is_device and pending and pending["signature"] == signature:
            # 受控去重：连续重复的设备状态只占一行，记录 repeat_count 与 last_hit。
            pending["repeat"] += 1
            pending["last"] = stamp
            if display_format == "unified":
                lines[pending["index"]] = f"{pending['base']}（repeat={pending['repeat']}）"
            else:
                lines[pending["index"]] = (
                    f"{pending['base']} | repeat={pending['repeat']} | last_hit={pending['last']}"
                )
            continue
        line = _audit_line(record, summary, since_prev=since_prev, display_format=display_format)
        lines.append(line)
        pending = {
            "signature": signature, "repeat": 1, "last": stamp,
            "index": len(lines) - 1, "base": line, "stamp": stamp,
        }
        last_scope, last_time = scope, stamp
    return "\n".join(lines).rstrip() + "\n" if lines else ""


# ---------------------------------------------------------------------------
# 设备状态事件（DEVICE）：从 raw marker 派生，或由适配器直接提交
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeviceMarker:
    source: str
    code: str
    pattern: re.Pattern[str]
    message: str
    kind: str = "STATE"
    host_from: str = "record"
    category: str | None = None
    state: str | None = None
    display: Mapping[str, Any] | None = None


class MarkerConfigError(ValueError):
    pass


def load_markers(value: Mapping[str, Any] | str | Path | None) -> tuple[list[DeviceMarker], re.Pattern[str] | None]:
    """加载 profile 提供的设备 marker 表；框架不内置项目 marker。"""
    if value is None:
        return [], None
    if isinstance(value, (str, Path)):
        payload = json.loads(Path(value).read_text(encoding="utf-8"))
    else:
        payload = dict(value)
    markers: list[DeviceMarker] = []
    for index, item in enumerate(payload.get("markers") or []):
        if not isinstance(item, Mapping):
            raise MarkerConfigError(f"marker[{index}] 必须是对象")
        for key in ("source", "code", "pattern"):
            if not item.get(key):
                raise MarkerConfigError(f"marker[{index}] 缺少 {key}")
        try:
            pattern = re.compile(str(item["pattern"]))
        except re.error as error:
            raise MarkerConfigError(f"marker[{index}] 正则不合法: {error}") from error
        markers.append(DeviceMarker(
            source=str(item["source"]),
            code=str(item["code"]).upper(),
            pattern=pattern,
            message=one_line(item.get("message") or str(item["code"])),
            kind=str(item.get("kind") or "STATE").upper(),
            host_from=str(item.get("host_from") or "record"),
            category=(str(item["category"]).upper() if item.get("category") else None),
            state=(str(item["state"]) if item.get("state") else None),
            display=(item["display"] if isinstance(item.get("display"), Mapping) else None),
        ))
    ts_pattern = payload.get("device_ts_pattern")
    device_ts = re.compile(str(ts_pattern)) if ts_pattern else DEVICE_TS_RE
    return markers, device_ts


def _raw_texts(raw: Any, source: str) -> list[str]:
    if not isinstance(raw, Mapping):
        return [str(raw)] if isinstance(raw, str) else []
    values: list[Any]
    if source == "*":
        values = list(raw.values())
    else:
        values = [raw.get(source)]
    texts: list[str] = []
    for value in values:
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, (list, tuple)):
            texts.extend(item for item in value if isinstance(item, str))
        elif isinstance(value, Mapping):
            texts.append(json.dumps(value, ensure_ascii=False, default=str))
    return texts


def _offset_moment(base: str | None, offset: Any) -> str | None:
    seconds = _num(offset)
    if not base or seconds is None:
        return None
    try:
        moment = datetime.strptime(base, "%Y-%m-%d %H:%M:%S.%f") + timedelta(seconds=float(seconds))
    except ValueError:
        return None
    return moment.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _marker_host_time(
    record: EventRecord,
    marker: DeviceMarker,
    raw: Any,
    round_power_on: Mapping[Any, str] | None = None,
) -> str | None:
    actual = record.actual if isinstance(record.actual, Mapping) else {}
    normalized = record.normalized if isinstance(record.normalized, Mapping) else {}
    if marker.host_from in {"play_start", "play_end"} and isinstance(raw, Mapping):
        direct = ensure_ms(raw.get(marker.host_from))
        if direct:
            return direct
    base = (round_power_on or {}).get(record.round)
    if marker.host_from == "play_start":
        return _offset_moment(base, normalized.get("play_start_offset_s"))
    if marker.host_from == "play_end":
        return _offset_moment(base, normalized.get("play_end_offset_s"))
    if marker.host_from == "power_on":
        return ensure_ms(actual.get("power_on_at")) or base
    if marker.host_from == "power_on_minus_off":
        power_on = ensure_ms(actual.get("power_on_at")) or base
        expected = record.expected if isinstance(record.expected, Mapping) else {}
        off_sec = _num(actual.get("off_sec")) or _num(expected.get("off_sec"))
        if power_on and off_sec:
            try:
                moment = datetime.strptime(power_on, "%Y-%m-%d %H:%M:%S.%f") - timedelta(seconds=float(off_sec))
                return moment.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            except ValueError:
                return None
    return ensure_ms(record.time)


def derive_device_events(
    records: Sequence[EventRecord],
    markers: Sequence[DeviceMarker],
    *,
    device_ts_pattern: re.Pattern[str] | None = None,
    round_power_on: Mapping[Any, str] | None = None,
    extension: "state_catalog.StateExtension | None" = None,
) -> list[EventRecord]:
    """从 TOOL 事件的 raw 中派生 DEVICE 状态事件。

    - 状态码先过通用目录/profile 扩展；未登记的 marker 码落 ``DEVICE_UNPARSED``，
      原始码保留在 ``details.raw_code``，不静默渲染成正式状态。
    - 同一事件里连续相同的 marker 合并为一条，保留 repeat_count 与 last_hit。
    - 无法解析的 raw 行不静默丢弃：调用方可用 :func:`device_unparsed_event` 显式提交。
    """
    if not markers:
        return []
    derived: list[EventRecord] = []
    ts_re = device_ts_pattern or DEVICE_TS_RE
    for record in records:
        raw = record.raw_payload
        if raw is None:
            continue
        for marker in markers:
            code, definition, catalog_status = state_catalog.resolve_device_code(marker.code, extension)
            unregistered = code == state_catalog.UNPARSED_CODE
            texts = _raw_texts(raw, marker.source)
            hits: list[dict[str, Any]] = []
            for text in texts:
                for match in marker.pattern.finditer(text):
                    fields = {key: value for key, value in match.groupdict().items() if not _skip(value)}
                    hits.append(fields)
            if not hits:
                continue
            timestamp = _marker_host_time(record, marker, raw, round_power_on)
            device_ts_match = ts_re.search(texts[0]) if texts else None
            device_ts = device_ts_match.group(1) if device_ts_match else None
            repeat = len(hits)
            details: dict[str, Any] = {
                "device": True,
                "audit_category": "DEVICE" if unregistered else (marker.category or definition.category.upper()),
                "audit_kind": marker.kind or "STATE",
                "marker": marker.code,
                "catalog_status": catalog_status,
                "repeat_count": repeat,
                "source_event": record.event,
                "source_event_seq": record.event_seq,
                "parameters": hits[0],
            }
            if unregistered:
                details["unparsed"] = True
                details["raw_code"] = marker.code
                details["raw_sha256"] = sha256_bytes((texts[0] if texts else marker.code).encode("utf-8"))
                details["raw_excerpt"] = _clip(one_line(texts[0] if texts else marker.code), 200)
            elif code != marker.code:
                details["raw_code"] = marker.code
            if repeat > 1:
                details["last_hit"] = timestamp
            if device_ts:
                details["device_ts"] = device_ts
            if marker.state:
                details["state"] = marker.state
            if not unregistered:
                base_display: dict[str, Any] = {
                    "category": state_catalog.display_category(code, definition),
                    "stage": state_catalog.DISPLAY_STAGE.get(code, ""),
                    "label": definition.label_zh,
                }
                if isinstance(marker.display, Mapping):
                    base_display.update({str(key): value for key, value in marker.display.items()})
                details["display"] = base_display
            evidence = list(record.evidence or [])
            if not evidence and record.raw_ref and record.raw_ref.get("path"):
                evidence = [{"path": record.raw_ref["path"]}]
            message = marker.message
            level = "INFO"
            if unregistered:
                message = f"未登记状态码 {marker.code}（{marker.message}）"
                level = "WARN"
            derived.append(EventRecord(
                event=code,
                time=timestamp or record.time,
                level=level,
                task_id=record.task_id,
                round=record.round,
                case_id=record.case_id,
                phase=record.phase,
                source="device",
                device=record.device,
                port_role=record.port_role,
                attempt=record.attempt,
                correlation_id=record.correlation_id,
                message=message,
                details=details,
                evidence=evidence,
            ))
    return derived


def device_unparsed_event(
    line: str,
    *,
    host_time: str | None = None,
    evidence: Sequence[Mapping[str, Any]] | None = None,
    task_id: str | None = None,
    round_value: Any = None,
) -> EventRecord:
    """未知设备行：保留摘要 + SHA-256 + 证据定位，禁止静默丢弃。"""
    text = one_line(line)
    digest = sha256_bytes(str(line).encode("utf-8"))
    return EventRecord(
        event="DEVICE_UNPARSED",
        time=ensure_ms(host_time) or now_human(),
        level="WARN",
        task_id=task_id,
        round=round_value,
        source="device",
        message=f"未识别设备行: {_clip(text, 160)}",
        details={"device": True, "audit_category": "DEVICE", "audit_kind": "STATE",
                 "raw_sha256": digest, "raw_excerpt": _clip(text, 200), "unparsed": True},
        evidence=normalize_evidence(list(evidence or [])),
    )


def write_device_event(
    writer: "EventLogWriter",
    line: str,
    *,
    code: str | None = None,
    state: str | None = None,
    category: str | None = None,
    message: str | None = None,
    device: str | None = None,
    port_role: str | None = None,
    host_time: str | None = None,
    evidence: Sequence[Mapping[str, Any]] | None = None,
    round_value: Any = None,
    extension: "state_catalog.StateExtension | None" = None,
) -> dict[str, Any]:
    """适配器提交设备行/设备状态。

    - 给出 ``code`` 时先按通用目录或 profile 扩展解析；未登记仍落 ``DEVICE_UNPARSED``，
      原始码写入 ``details.raw_code``，原始行摘要与 sha256 一并保留。
    - 不给 ``code`` 时保持旧行为：整行按未识别设备行记 ``DEVICE_UNPARSED``。
    """
    text = one_line(line)
    if code:
        semantic, definition, catalog_status = state_catalog.resolve_device_code(code, extension)
        unregistered = semantic == state_catalog.UNPARSED_CODE
        details: dict[str, Any] = {
            "device": True,
            "audit_kind": "STATE",
            "marker": str(code).upper(),
            "catalog_status": catalog_status,
            "raw_code": str(code).upper(),
            "raw_sha256": sha256_bytes(str(line).encode("utf-8")),
            "raw_excerpt": _clip(text, 200),
        }
        if state:
            details["state"] = state
        if unregistered:
            details["audit_category"] = "DEVICE"
            details["unparsed"] = True
            default_message = f"未登记状态码 {str(code).upper()}：{text}"
            level = "WARN"
        else:
            details["audit_category"] = (category or definition.category).upper()
            details["display"] = {
                "category": state_catalog.display_category(semantic, definition),
                "stage": state_catalog.DISPLAY_STAGE.get(semantic, ""),
                "label": definition.label_zh,
            }
            default_message = f"{definition.label_zh}：{text}"
            level = "INFO"
        return writer.emit(
            semantic,
            time=ensure_ms(host_time) or now_human(), level=level, round=round_value,
            source="device", device=device, port_role=port_role,
            message=_clip(message or default_message, 240), details=details,
            evidence=normalize_evidence(list(evidence or [])),
        )
    record = device_unparsed_event(line, host_time=host_time, evidence=evidence,
                                   task_id=writer.task_id, round_value=round_value)
    payload = writer.emit(
        record.event,
        time=record.time, level=record.level, round=record.round,
        source=record.source, message=record.message, details=record.details,
        evidence=record.evidence,
    )
    if device:
        payload["device"] = device
    if port_role:
        payload["port_role"] = port_role
    return payload


def _inline_value(value: Any, limit: int = 160) -> str:
    if isinstance(value, Mapping):
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        if len(encoded) > limit:
            return f"<{len(value)} keys>"
        return encoded
    if isinstance(value, (list, tuple)):
        encoded = json.dumps(value, ensure_ascii=False, default=str)
        if len(encoded) > limit:
            return f"<{len(value)} items>"
        return encoded
    return _text(value)


def _fmt_pct(value: Any, numerator: int | None = None, denominator: int | None = None) -> str:
    if value is None:
        return "无数据"
    if numerator is not None and denominator is not None:
        return f"{value}%（{numerator}/{denominator}）"
    return f"{value}%"


def _fmt_seconds(value: Any) -> str:
    return "无数据" if value is None else f"{_text(value)} s"


def render_summary_md(summary: Mapping[str, Any]) -> str:
    """渲染 summary.md。每一行都来自 summary.json，禁止另行编写结论。"""
    meta = summary.get("meta", {})
    rates = summary.get("rates", {})
    counts = summary.get("counts", {})
    gate = summary.get("gate") or {}
    environment = summary.get("environment") or {}
    tooling = summary.get("tooling") or {}
    latency = summary.get("latency") or {}
    responsibility = summary.get("responsibility") or {}
    distribution = rates.get("attempt_distribution") or {}
    rounds_total = counts.get("rounds_total", 0) or 0

    lines: list[str] = []
    lines.append(f"# {meta.get('title') or 'lstest 测试摘要'}")
    lines.append("")
    lines.append("## 一句话结论")
    lines.append(
        f"`test_outcome={summary.get('test_outcome')}`，"
        f"`delivery_status={summary.get('delivery_status')}`，"
        f"`final_status={summary.get('final_status')}`。"
        f"{summary.get('final_status_rule')}。"
    )
    lines.append("")
    lines.append("## 1. 测了什么、为什么、范围")
    lines.append("")
    lines.append(f"- 测试对象：{meta.get('title') or '未提供'}")
    lines.append(f"- 目的：{meta.get('purpose') or '未提供（工具侧未记录 purpose）'}")
    lines.append(f"- 范围/模式：mode={meta.get('mode') or '-'}；目标轮次={rates.get('rounds_target') or '未声明'}")
    lines.append(f"- 实际完成：{rounds_total} 轮；说明：{summary.get('test_outcome_basis')}")
    if meta.get("scope"):
        lines.append(f"- 范围补充：{meta['scope']}")
    lines.append("")
    lines.append("## 2. 测试对象与环境")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    for label, value in (
        ("节点", environment.get("node")),
        ("操作系统", environment.get("os")),
        ("Python", environment.get("python")),
        ("执行方式", environment.get("runtime")),
        ("工具/脚本", tooling.get("tool") or tooling.get("source")),
        ("profile", " / ".join(str(item) for item in (tooling.get("profile_id"), tooling.get("profile_version")) if item)),
        ("合同", tooling.get("contract")),
        ("任务目录", (summary.get("parameters") or {}).get("task_dir")),
        ("结果目录", (summary.get("parameters") or {}).get("result_dir")),
    ):
        lines.append(f"| {label} | {_md(value)} |")
    inputs = summary.get("inputs") or []
    if inputs:
        lines.append("")
        lines.append("| 输入 | SHA-256 | 格式 |")
        lines.append("|---|---|---|")
        for item in inputs:
            lines.append(f"| {_md(item.get('path'))} | `{_md(item.get('sha256'))}` | {_md(_inline_value(item.get('format')) if item.get('format') else None)} |")
    lines.append("")
    lines.append("## 3. 最终结论与签发依据")
    lines.append("")
    lines.append("判定口径：`test_outcome` 只看轮次结果；`delivery_status` 只看 gate 的交付件；两者都为真才允许 `final_status=PASS`。")
    lines.append("")
    if gate:
        lines.append(f"gate 来源：`{gate.get('source_event')}`（event_seq={gate.get('source_event_seq')}，evaluated_at={gate.get('evaluated_at')}），记录状态 `{gate.get('recorded_status')}`。")
        lines.append("")
        lines.append("| gate 检查项 | 结果 |")
        lines.append("|---|---|")
        for key, value in (gate.get("checks") or {}).items():
            lines.append(f"| {key} | {'PASS' if value else 'FAIL'} |")
        lines.append("")
        lines.append(f"`gate_pass={_text(gate.get('gate_pass'))}`；未通过项：{', '.join(gate.get('failed_checks') or []) or '无'}。")
    else:
        lines.append("未找到 gate：不能签发，按 `NEEDS_REVIEW` 处理。")
    conflicts = summary.get("conflicts") or []
    if conflicts:
        lines.append("")
        lines.append("状态冲突：")
        for item in conflicts:
            lines.append(f"- `{item['code']}`：{item['detail']} → 已按 `{item['resolved_as']}` 处理")
    lines.append("")
    lines.append("## 3.1 设备状态目录")
    lines.append("")
    state_report = summary.get("state_report") or {}
    if not state_report or not state_report.get("device_events"):
        lines.append("无设备状态事件。")
    else:
        catalog = state_report.get("catalog") or {}
        lines.append(
            f"目录：通用 {catalog.get('common_states')} 条；"
            f"项目 `{catalog.get('profile_id') or '未加载'}` "
            f"{catalog.get('project_states', 0)} 条；设备事件 {state_report.get('device_events')} 条。"
        )
        lines.append("")
        lines.append("| 类别 | 事件数 |")
        lines.append("|---|---|")
        for category, count in (state_report.get("by_category") or {}).items():
            lines.append(f"| {category} | {count} |")
        status = state_report.get("catalog_status") or {}
        lines.append("")
        lines.append(
            f"目录校验：通用 {status.get('common', 0)} / 项目 {status.get('project', 0)} / "
            f"未登记 {status.get('unregistered', 0)}；"
            f"重复合并 {((state_report.get('duplicates') or {}).get('collapsed_events', 0))} 条；"
            f"迟到 {len(state_report.get('late') or [])} 条；"
            f"前置缺失 {len(state_report.get('prerequisite_missing') or [])} 条；"
            f"未恢复阻断 {len((state_report.get('blocking') or {}).get('unrecovered') or [])} 条。"
        )
        required = state_report.get("required_states") or {}
        if required.get("declared"):
            lines.append("")
            lines.append(
                f"必需状态：{', '.join(required.get('declared') or [])}；"
                f"已出现 {', '.join(required.get('present') or []) or '无'}；"
                f"缺失 {', '.join(required.get('missing') or []) or '无'}。"
            )
    lines.append("")
    lines.append("## 4. 结果统计")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 完成轮次 | {rounds_total} / {rates.get('rounds_target') or '?'} |")
    lines.append(f"| PASS / FAIL / BLOCKED / 其他 | {counts.get('pass')} / {counts.get('fail')} / {counts.get('blocked')} / {counts.get('other')} |")
    lines.append(f"| round_success_rate | {_fmt_pct(rates.get('round_success_rate'), counts.get('pass'), rounds_total)} |")
    lines.append(
        f"| first_attempt_success_rate | "
        f"{_fmt_pct(rates.get('first_attempt_success_rate'), (distribution.get('counts') or {}).get('1'), rounds_total)} |"
    )
    lines.append(f"| 平均尝试次数 | {_text(rates.get('avg_attempts'))} |")
    lines.append(f"| 尝试总数 | {_text(rates.get('attempts_total'))} |")
    lines.append("")
    lines.append("首次成功尝试分布（按第几次播报成功统计）：")
    lines.append("")
    lines.append("| 第 N 次成功 | 轮数 | 占比 |")
    lines.append("|---|---|---|")
    distribution_counts = distribution.get("counts") or {}
    zero_keys = []
    for key, count in distribution_counts.items():
        if key == "none":
            lines.append(f"| 10 次均未唤醒 | {count} | {_text((distribution.get('percent') or {}).get(key))}% |")
        elif count:
            lines.append(f"| 第 {key} 次 | {count} | {_text((distribution.get('percent') or {}).get(key))}% |")
        else:
            zero_keys.append(key)
    if zero_keys:
        lines.append(f"| 其余 {len(zero_keys)} 个尝试次数 | 0 | 0% |")
    lines.append("")
    lines.append("耗时趋势（前一半 vs 后一半，变化超过 ±10% 判为退化/改善）：")
    lines.append("")
    lines.append("| 指标 | 首次 | 末次 | 均值 | 方向 | 变化 |")
    lines.append("|---|---|---|---|---|---|")
    for item in latency.values():
        if not isinstance(item, Mapping) or "direction" not in item:
            continue
        delta = "样本不足" if item.get("direction") == "insufficient" else f"{_text(item.get('delta_pct'))}%"
        lines.append(
            f"| {item.get('label')} | {_md(_inline_value(item.get('first')))} | {_md(_inline_value(item.get('last')))} "
            f"| {_md(_inline_value(item.get('mean')))} | {item.get('direction')} "
            f"| {delta} |"
        )
    lines.append("")
    lines.append("## 5. 异常与趋势")
    lines.append("")
    anomalies = summary.get("anomalies") or []
    if not anomalies:
        lines.append("未记录异常状态事件。")
    else:
        lines.append("| 异常码 | 事件 | 次数 | 涉及轮次 | 是否恢复 | 说明 |")
        lines.append("|---|---|---|---|---|---|")
        for item in anomalies:
            rounds_text = ",".join(str(value) for value in item.get("rounds") or []) or "-"
            lines.append(
                f"| {item['code']} | {item['event']} | {item['count']} | {rounds_text} | "
                f"{'是' if item.get('recovered') else '否'} | {_md(item.get('example') or item.get('reason'))} |"
            )
        degrading = [item for item in latency.values()
                     if isinstance(item, Mapping) and item.get("direction") == "degrading"]
        if degrading:
            lines.append("")
            lines.append("退化趋势：" + "；".join(
                f"{item.get('label')} 后段比前段恶化 {item.get('delta_pct')}%" for item in degrading
            ))
        gap = rates.get("first_attempt_gap_pct")
        if gap is not None and gap > 0:
            lines.append("")
            lines.append(
                f"**首轮稳定性缺口 {gap} 个百分点**：最终成功率 {_text(rates.get('round_success_rate'))}%"
                f" 但首轮成功率仅 {_text(rates.get('first_attempt_success_rate'))}%；"
                "只报最终成功率会掩盖该退化信号。"
            )
    lines.append("")
    lines.append("## 6. 首败 / 最差轮次 / 责任层 / 证据")
    lines.append("")
    first = summary.get("first_failure")
    worst = summary.get("worst_round")
    lines.append(f"- 首败：{_round_line(first) if first else '无（没有 FAIL/BLOCKED 轮次）'}")
    lines.append(f"- 最差轮次：{_round_line(worst) if worst else '无尝试统计'}")
    lines.append(f"- 责任层：`{responsibility.get('layer', 'unknown')}`，依据：{responsibility.get('basis') or '未判定'}")
    for suspect in responsibility.get("suspects") or []:
        lines.append(f"  - 待隔离：{suspect}")
    lines.append("")
    lines.append("## 7. 还缺什么 / 下一步")
    lines.append("")
    missing = summary.get("missing") or []
    lines.append("缺失项：")
    for item in missing or ["无"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("下一步：")
    for item in summary.get("next_steps") or []:
        lines.append(f"1. {item}")
    lines.append("")
    lines.append("## 8. 证据索引")
    lines.append("")
    lines.append("| 用途 | 路径 | 行/序号 | SHA-256 |")
    lines.append("|---|---|---|---|")
    for item in summary.get("evidence_index") or []:
        lines.append(
            f"| {item.get('purpose')} | `{_md(item.get('path'))}` | "
            f"{','.join(str(value) for value in item.get('lines') or []) or '-'} | {_md(item.get('sha256'))} |"
        )
    lines.append("")
    lines.append(
        f"事件机器真源：`events/events.jsonl`（v3）或 `events.jsonl`（v2），"
        f"{meta.get('events_count')} 条，schema `{EVENT_SCHEMA}`。"
    )
    lines.append("")
    return "\n".join(lines)


def _md(value: Any) -> str:
    if _skip(value):
        return "-"
    return str(value).replace("|", "\\|")


def _round_line(item: Mapping[str, Any]) -> str:
    return (
        f"R{item.get('round')} status={item.get('status')} attempts={_text(item.get('attempts_total'))} "
        f"first={_text(item.get('first_success_attempt'))} reason={item.get('reason') or '-'}"
    )


# ---------------------------------------------------------------------------
# 落盘与校验
# ---------------------------------------------------------------------------

def write_bundle(records: Sequence[EventRecord], out_dir: Path, *,
                 meta: Mapping[str, Any] | None = None,
                 events_path: Path | None = None,
                 extension: "state_catalog.StateExtension | None" = None,
                 layout: str | None = None) -> dict[str, Any]:
    """写 tool.log / summary.md / summary.json；返回落盘的 summary。

    ``layout`` 决定产物位置：v3 为 ``events/tool.log`` + ``reports/summary.json``，
    v2 为旧根目录。未指定时按目录现状识别（空目录按 v3）。
    ``tool.log`` 是完整动作审计（每个事件一行），不是摘要；结论只存在于 summary。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    records = list(records)
    if layout is None:
        layout = result_layout.detect_layout(out_dir)
        if layout == result_layout.LAYOUT_V1:
            layout = result_layout.LAYOUT_V3
    events = Path(events_path) if events_path else result_layout.path(out_dir, "events", layout)
    context = dict(meta or {})
    if events.is_file():
        context.setdefault("events_sha256", sha256_file(events))
    summary = summarize(records, meta=context, extension=extension)
    payload = public_summary(summary)
    summary_target = result_layout.path(out_dir, "summary_json", layout)
    summary_target.parent.mkdir(parents=True, exist_ok=True)
    summary_target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "summary.md").write_text(render_summary_md(summary), encoding="utf-8")
    tool_target = result_layout.path(out_dir, "tool_log", layout)
    tool_target.parent.mkdir(parents=True, exist_ok=True)
    tool_target.write_text(render_tool_log(summary, records), encoding="utf-8")
    if layout == result_layout.LAYOUT_V3:
        result_layout.write_readme(out_dir, task_id=summary.get("task_id"))
        result_layout.write_manifest(out_dir, task_id=summary.get("task_id"))
    return payload


def verify_bundle(run_dir: Path, *, check_files: bool = False,
                  extension: "state_catalog.StateExtension | None" = None,
                  strict_states: bool = False) -> list[dict[str, str]]:
    """校验一个 v2 运行目录；返回 findings（code/level/message）。空列表 = 通过。"""
    run_dir = Path(run_dir)
    findings: list[dict[str, str]] = []

    def add(code: str, level: str, message: str) -> None:
        findings.append({"code": code, "level": level, "message": message})

    layout = result_layout.detect_layout(run_dir)
    events_found = result_layout.resolve(run_dir, "events")
    if events_found is None:
        add("MISSING_EVENTS", "ERROR",
            "缺少 events.jsonl（v3: events/events.jsonl；v2: 根目录 events.jsonl）")
        return findings
    events_path = events_found
    if layout != result_layout.LAYOUT_V3:
        add("LAYOUT_LEGACY", "WARN",
            f"旧结果布局 {layout}：可读，建议用 `lstest.py result migrate` 迁移到 v3")
    else:
        report = result_layout.audit(run_dir)
        for item in report["violations"]:
            # v3 目录内根目录仍散落旧 JSON/日志即违反交付契约；旧布局目录只告警。
            add("LAYOUT_VIOLATION", "ERROR",
                f"根目录存在 {item['reason']} 项 {item['name']}；建议：{item['suggestion']}")
        for rel in report["missing"]:
            add("LAYOUT_MISSING", "ERROR", f"v3 布局缺少 {rel}")
        for item in result_layout.verify_manifest(run_dir):
            add(item["code"], item["level"], item["message"])
    try:
        records = read_events(events_path)
    except LegacyFormatError as error:
        add("BAD_EVENTS", "ERROR", str(error))
        return findings
    if not records:
        add("EMPTY_EVENTS", "ERROR", "events.jsonl 没有事件")
        return findings

    for index, record in enumerate(records, start=1):
        if record.event_seq != index:
            add("EVENT_SEQ", "ERROR", f"第 {index} 条事件 event_seq={record.event_seq}，应为 {index}")
        if not record.time:
            add("MISSING_TIME", "ERROR", f"event_seq={record.event_seq} 缺少 time")
        elif not TIME_RE.match(record.time):
            add("TIME_FORMAT", "ERROR", f"event_seq={record.event_seq} 时间格式不合规: {record.time}")
        if record.event == "UNKNOWN":
            add("UNKNOWN_EVENT", "WARN", f"event_seq={record.event_seq} 事件名为 UNKNOWN（旧日志缺字段）")
        if record.legacy_warnings:
            for warning in record.legacy_warnings:
                add("LEGACY_WARNING", "WARN", f"event_seq={record.event_seq}: {warning}")
        for ref in record.evidence or []:
            path = ref.get("path") or ""
            if ABS_WIN_RE.match(path) or ABS_POSIX_RE.search(path):
                add("ABS_PATH", "ERROR", f"event_seq={record.event_seq} 证据含绝对路径: {path}")
            if check_files:
                target = result_layout.resolve_relative(run_dir, path)
                if target is None or not target.is_file():
                    add("MISSING_EVIDENCE", "WARN", f"证据文件不存在: {path}")
                elif ref.get("sha256"):
                    if sha256_file(target) != str(ref["sha256"]).lower():
                        add("EVIDENCE_SHA", "ERROR", f"证据 SHA-256 不匹配: {path}")
        if record.raw_ref and record.raw_ref.get("path"):
            target = result_layout.resolve_relative(run_dir, record.raw_ref["path"])
            if target is None or not target.is_file():
                add("MISSING_RAW", "ERROR", f"raw 引用不存在: {record.raw_ref['path']}")
            elif record.raw_ref.get("sha256") and sha256_file(target) != record.raw_ref["sha256"]:
                add("RAW_SHA", "ERROR", f"raw SHA-256 不匹配: {record.raw_ref['path']}")

    summary_path = result_layout.resolve(run_dir, "summary_json")
    if summary_path is None or not summary_path.is_file():
        add("MISSING_SUMMARY", "ERROR", "缺少 summary.json（v3: reports/summary.json）")
        return findings
    try:
        stored = json.loads(summary_path.read_text(encoding="utf-8"))
    except ValueError as error:
        add("BAD_SUMMARY", "ERROR", f"summary.json 不是合法 JSON: {error}")
        return findings

    tool_log_path = result_layout.resolve(run_dir, "tool_log")
    if tool_log_path is None:
        add("MISSING_OUTPUT", "ERROR", "缺少 tool.log（v3: events/tool.log）")
    if not (run_dir / "summary.md").is_file():
        add("MISSING_OUTPUT", "ERROR", "缺少 summary.md")

    regenerated = summarize(records, meta={
        "title": (stored.get("meta") or {}).get("title"),
        "purpose": (stored.get("meta") or {}).get("purpose"),
        "scope": (stored.get("meta") or {}).get("scope"),
        "mode": (stored.get("meta") or {}).get("mode"),
        "events_sha256": sha256_file(events_path),
        "inputs": stored.get("inputs") or [],
        "environment": stored.get("environment") or {},
        "tool": (stored.get("tooling") or {}).get("tool"),
        "profile_id": (stored.get("tooling") or {}).get("profile_id"),
    }, extension=extension)
    expected = public_summary(regenerated)
    for key in sorted(set(expected) | set(stored)):
        if key == "meta":
            continue
        if key not in stored and key == "state_report":
            add("SUMMARY_DRIFT", "WARN",
                "旧版本 summary.json 缺少 state_report；重新 render 即可补齐")
            continue
        if expected.get(key) != stored.get(key):
            add("SUMMARY_DRIFT", "ERROR", f"summary.json 与事件重算不一致: {key}")
    stored_meta = stored.get("meta") or {}
    if stored_meta.get("events_count") != expected.get("meta", {}).get("events_count"):
        add("SUMMARY_DRIFT", "WARN", "summary.json meta.events_count 与事件重算不一致")
    if stored.get("task_id") != expected.get("task_id"):
        add("SUMMARY_DRIFT", "WARN", "summary.json task_id 与事件重算不一致")

    if tool_log_path is not None and tool_log_path.is_file():
        tool_log = tool_log_path.read_text(encoding="utf-8")
        display_format = ("unified" if str((stored.get("meta") or {}).get("display_format", "")).startswith("unified")
                          else "legacy")
        rendered = render_tool_log(regenerated, records, display_format=display_format)
        if rendered != tool_log:
            add("TOOL_LOG_DRIFT", "ERROR", "tool.log 与事件重算不一致（疑似手工修改）")
        for problem in validate_tool_log_text(tool_log):
            add("TOOL_LOG_FORMAT", "ERROR", problem)

    gate = expected.get("gate") or {}
    if gate and gate.get("gate_pass") is False and expected.get("final_status") == "PASS":
        add("GATE_CONFLICT", "ERROR", "gate_pass=false 却判定 final_status=PASS")
    if expected.get("test_outcome") == "PASS" and (expected.get("counts") or {}).get("fail"):
        add("OUTCOME_CONFLICT", "ERROR", "test_outcome=PASS 但存在 FAIL 轮次")
    for item in expected.get("conflicts") or []:
        add("SOURCE_STATUS_CONFLICT", "WARN", item.get("detail") or "源事件状态冲突")
    failures = [item for item in records if (item.status or "").upper() in STATUS_FAILURE]
    if failures:
        add("SOURCE_FAILURE_EVENTS", "WARN", f"源日志含 {len(failures)} 条失败状态事件")

    scan_targets = [events_path, tool_log_path, run_dir / "summary.md", summary_path]
    for path in [item for item in scan_targets if item is not None and item.is_file()]:
        name = path.relative_to(run_dir).as_posix() if path.is_relative_to(run_dir) else path.name
        text = path.read_text(encoding="utf-8", errors="replace")
        if ABS_WIN_RE.search(text):
            add("ABS_PATH", "ERROR", f"{name} 含 Windows 绝对路径")
        elif ABS_POSIX_RE.search(text):
            add("ABS_PATH", "ERROR", f"{name} 含 POSIX 绝对路径")
        if re.search(r"(?i)\b(access_key_secret|access_key_id|lstts_tts_token|lstest_tts_token)\b\s*[:=]\s*\S", text):
            add("SECRET", "ERROR", f"{name} 疑似包含凭据值")
    # 状态目录：未登记码 / 迟到 / 前置缺失 / 未恢复阻断。
    state_report = expected.get("state_report") or {}
    unregistered_level = "ERROR" if strict_states else "WARN"
    for item in state_report.get("unregistered") or []:
        add("STATE_UNREGISTERED", unregistered_level,
            f"event_seq={item.get('event_seq')} 设备状态码未登记: {item.get('code')}"
            f"（raw_code={item.get('raw_code')}）")
    for item in state_report.get("late") or []:
        add("STATE_LATE", "WARN",
            f"{item.get('code')} 的 device_ts={item.get('device_ts')} 早于上一条设备事件"
            f"（host={item.get('time')}）")
    if state_report.get("prerequisites_strict"):
        for item in state_report.get("prerequisite_missing") or []:
            add("STATE_PREREQUISITE", "WARN",
                f"{item.get('code')} 缺少前置状态: {', '.join(item.get('missing') or [])}")
    for item in (state_report.get("blocking") or {}).get("unrecovered") or []:
        add("STATE_UNRECOVERED", unregistered_level,
            f"{item.get('code')} 处于阻断状态且窗口内无恢复（host={item.get('time')}）")
    for code, value in sorted((state_report.get("gate_checks") or {}).items()):
        if value is False:
            add("STATE_GATE", "WARN", f"状态 gate 未通过: {code}")

    # 同一缺失证据只报一次，避免旧日志批量导入时刷屏。
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, str]] = []
    for item in findings:
        key = (item["code"], item["message"])
        if item["code"] in {"MISSING_EVIDENCE", "MISSING_RAW"} and key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def schema_document() -> dict[str, Any]:
    """给工具侧/校验器读取的自描述 schema。"""
    return {
        "event_schema": EVENT_SCHEMA,
        "summary_schema": SUMMARY_SCHEMA,
        "bundle_version": BUNDLE_VERSION,
        "event_fields": list(EVENT_FIELDS),
        "required_fields": list(REQUIRED_FIELDS),
        "numeric_fields": sorted(NUMERIC_FIELDS),
        "status_success": sorted(STATUS_SUCCESS),
        "status_failure": sorted(STATUS_FAILURE),
        "attempt_statuses": sorted(ATTEMPT_STATUSES),
        "test_outcome": list(TEST_OUTCOME_VALUES),
        "delivery_status": list(DELIVERY_STATUS_VALUES),
        "final_status": list(FINAL_STATUS_VALUES),
        "artifact_gate_checks": sorted(ARTIFACT_GATE_CHECKS),
        "blocking_gate_checks": sorted(BLOCKING_GATE_CHECKS),
        "raw_externalization": (
            "所有 raw 写入 evidence/raw/e<seq>_<EVENT>.json（v3）或 raw/（v2），"
            "events 只保留 path/sha256/bytes/lines"
        ),
        "tool_log_rule": "tool.log 每个事件恰好一行，按 host 毫秒排序，含做法/状态/判断/证据，禁止续行与摘要",
        "tool_log_line": "<YYYY-MM-DD HH:MM:SS.mmm> [<范围>][<动作>][<阶段>]([<结果>]) <描述> | k=v | evidence=<路径#行号>",
        "device_rule": "DEVICE 事件与 TOOL 事件同一毫秒时序；连续重复状态合并并记 repeat_count/last_hit",
        "layout": result_layout.layout_document(),
        "state_catalog": state_catalog.schema_document(),
        "state_rule": (
            "设备状态码必须登记在通用目录或 lstest.state-extension.v1 项目扩展；"
            "未登记码落 DEVICE_UNPARSED 并保留 raw_code，禁止静默渲染"
        ),
    }
