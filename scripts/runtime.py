"""公共场景运行时边界。

该模块只处理设备无关的播放、观察窗口、证据边界和结果落盘动作。
项目适配层负责串口事件读取、marker 解析以及业务 oracle，并通过回调注入。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .core import CaseResult, TaskArtifacts, sha256_file
from .observations import RawTag, ToolJudgement, judge_wakeup
from .playback import PlaybackBackend


@dataclass
class BroadcastRecognitionTracker:
    """Enforces the one broadcast to one recognition contract for a task."""

    broadcasts: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending: list[str] = field(default_factory=list)
    recognition_count: dict[str, int] = field(default_factory=dict)
    last_broadcast_id: str = ""

    def begin_broadcast(
        self,
        broadcast_id: str,
        *,
        case_id: str,
        audio_file: Path,
        audio_sha256: str,
        expected_recognition: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        key = str(broadcast_id).strip()
        if not key:
            raise ValueError("broadcast_id cannot be empty")
        if key in self.broadcasts:
            raise ValueError(f"duplicate broadcast_id: {key}")
        record = {
            "broadcast_id": key,
            "case_id": case_id,
            "audio_file": str(audio_file),
            "audio_sha256": audio_sha256,
            "expected_recognition": dict(expected_recognition or {}),
            "started_monotonic": time.monotonic(),
        }
        self.broadcasts[key] = record
        self.pending.append(key)
        self.recognition_count[key] = 0
        self.last_broadcast_id = key
        return record

    def discard_broadcast(self, broadcast_id: str) -> None:
        """Discard a failed host playback so later results stay visibly unmatched."""
        self.broadcasts.pop(broadcast_id, None)
        self.recognition_count.pop(broadcast_id, None)
        if broadcast_id in self.pending:
            self.pending.remove(broadcast_id)
        if self.last_broadcast_id == broadcast_id:
            self.last_broadcast_id = ""

    def observe_recognition(self, broadcast_id: str | None = None, *, case_id: str = "") -> tuple[str | None, str | None, int]:
        """Return (broadcast id, anomaly code, number of results for that broadcast)."""
        key = str(broadcast_id).strip() if broadcast_id else ""
        if not key:
            matching_pending = [
                item for item in self.pending
                if not case_id or self.broadcasts.get(item, {}).get("case_id") == case_id
            ]
            if matching_pending:
                key = matching_pending[0]
            elif case_id and self.last_broadcast_id and self.broadcasts.get(self.last_broadcast_id, {}).get("case_id") == case_id:
                # A second result within the same case must remain attributable
                # to that broadcast instead of being mistaken for an orphan.
                key = self.last_broadcast_id
        if not key or key not in self.broadcasts:
            return None, "UNEXPECTED_RECOGNITION", 0
        self.recognition_count[key] = self.recognition_count.get(key, 0) + 1
        count = self.recognition_count[key]
        if count == 1:
            if key in self.pending:
                self.pending.remove(key)
            return key, None, count
        return key, "MULTIPLE_RECOGNITIONS_FOR_PLAYBACK", count


@dataclass
class WakeWordSequence:
    """Ordered wake word requirements loaded from the project requirements table."""

    requirements: list[dict[str, Any]] = field(default_factory=list)
    index: int = 0

    @classmethod
    def from_items(cls, values: Iterable[Mapping[str, Any]]) -> "WakeWordSequence":
        items = [dict(item) for item in values]
        seen: set[str] = set()
        for item in items:
            wake_word_id = str(item.get("wake_word_id") or "").strip()
            if not wake_word_id or not str(item.get("spoken_text") or "").strip() or not dict(item.get("expected_raw") or {}):
                raise ValueError("each wake word requires wake_word_id, spoken_text, and expected_raw")
            if wake_word_id in seen:
                raise ValueError(f"duplicate wake_word_id: {wake_word_id}")
            seen.add(wake_word_id)
        return cls(items)

    def current(self) -> dict[str, Any]:
        if self.index >= len(self.requirements):
            raise RuntimeError("all configured wake words have already been verified")
        return dict(self.requirements[self.index])

    def matches_current(self, wake_word_id: str) -> bool:
        if not self.requirements:
            return True
        current = self.current()
        return wake_word_id == str(current["wake_word_id"])

    def advance(self) -> None:
        if not self.requirements:
            return
        self.current()
        self.index += 1


class ScenarioRuntime:
    """为项目场景提供统一、可复用的运行时动作边界。"""

    def __init__(
        self,
        artifacts: TaskArtifacts,
        *,
        playback_device_key: str | None = None,
        playback_script: Path | None = None,
        wake_words: Iterable[Mapping[str, Any]] = (),
    ) -> None:
        self.artifacts = artifacts
        self.player = PlaybackBackend(artifacts, playback_device_key, playback_script)
        self.broadcast_tracker = BroadcastRecognitionTracker()
        self._recognition_associations: dict[str, list[dict[str, Any]]] = {}
        self.wake_word_sequence = WakeWordSequence.from_items(wake_words)

    def current_wake_word(self) -> dict[str, Any]:
        """Return the only wake word currently allowed by the ordered requirements table."""
        return self.wake_word_sequence.current()

    def probe(self) -> dict[str, Any]:
        """探测显式播放设备，失败时返回可审计结果。"""
        ok = self.player.probe()
        result = {
            "status": "PASS" if ok else "BLOCKED",
            "device_key": self.player.device_key or "",
            "playback_target": self.player.target_label,
            "playback_target_mode": self.player.target_mode,
        }
        if not ok:
            result["reason"] = "playback_probe_failed"
            raise RuntimeError("BLOCKED_AUDIO_DEVICE: lstest playback probe failed")
        return result

    def play(
        self,
        audio_file: Path,
        *,
        case_id: str = "",
        expected_recognition: Mapping[str, Any] | None = None,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        """播放音频，并建立与期望原始识别字段一一对应的播报窗口。"""
        broadcast_id = f"broadcast-{len(self.broadcast_tracker.broadcasts) + 1:06d}"
        audio_sha256 = sha256_file(audio_file) if audio_file.is_file() else ""
        broadcast = self.broadcast_tracker.begin_broadcast(
            broadcast_id,
            case_id=case_id,
            audio_file=audio_file,
            audio_sha256=audio_sha256,
            expected_recognition=expected_recognition,
        )
        self.artifacts.emit(
            "BROADCAST_STARTED", message=f"播报窗口 {broadcast_id} 已建立。", task_log=True, **broadcast,
        )
        try:
            ok = self.player.play(audio_file, case_id=case_id, timeout=timeout)
        except Exception as error:
            self.broadcast_tracker.discard_broadcast(broadcast_id)
            self.artifacts.emit(
                "BROADCAST_FAILED", level="ERROR", message=f"播报窗口 {broadcast_id} 未成功播放。",
                task_log=True, case_id=case_id, error=f"{type(error).__name__}: {error}",
            )
            return {
                "returncode": 1,
                "error": f"{type(error).__name__}: {error}",
                "audio_file": str(audio_file),
            }
        result: dict[str, Any] = {
            "returncode": 0 if ok else 1,
            "audio_file": str(audio_file),
            "audio_sha256": audio_sha256,
            "broadcast_id": broadcast_id,
        }
        if not ok:
            self.broadcast_tracker.discard_broadcast(broadcast_id)
            self.artifacts.emit(
                "BROADCAST_FAILED", level="ERROR", message=f"播报窗口 {broadcast_id} 未成功播放。",
                task_log=True, case_id=case_id, error="playback_failed",
            )
            result["error"] = "playback_failed"
        return result

    def observe_recognition(
        self,
        recognition: Any,
        *,
        case_id: str = "",
        broadcast_id: str | None = None,
        evidence_refs: Iterable[str] = (),
        suppress_content_mismatch: bool = False,
    ) -> dict[str, Any]:
        """Associate one project recognition result with one host broadcast window.

        Project adapters must call this exactly once for every final recognition
        result they observe.  Omit ``broadcast_id`` to consume the oldest
        unmatched broadcast.  An unmatched result or a second result for the
        same broadcast is recorded as a task anomaly immediately.
        """
        assigned_id, anomaly_code, count = self.broadcast_tracker.observe_recognition(broadcast_id, case_id=case_id)
        refs = [str(item) for item in evidence_refs]
        payload = {
            "broadcast_id": assigned_id or "",
            "case_id": case_id,
            "recognition": recognition,
            "recognition_count": count,
            "evidence_refs": refs,
        }
        expected = dict(self.broadcast_tracker.broadcasts.get(assigned_id or "", {}).get("expected_recognition") or {})
        recognition_values = dict(recognition) if isinstance(recognition, Mapping) else {}
        content_mismatch = not suppress_content_mismatch and bool(expected) and any(
            recognition_values.get(name) != value for name, value in expected.items()
        )
        anomaly_codes = [code for code in (anomaly_code,) if code]
        if assigned_id and not expected:
            anomaly_codes.append("MISSING_EXPECTED_RECOGNITION_CONTRACT")
        if anomaly_code:
            message = (
                "发现未关联播报的识别结果。"
                if anomaly_code == "UNEXPECTED_RECOGNITION"
                else f"播报窗口 {assigned_id} 出现第 {count} 个识别结果。"
            )
            anomaly = self.artifacts.record_anomaly(anomaly_code, message, **payload)
            self.append_jsonl("broadcast_recognition_anomalies.jsonl", {**anomaly, **payload})
        if content_mismatch:
            content_code = "RECOGNITION_RESULT_MISMATCH"
            anomaly_codes.append(content_code)
            mismatch_payload = {**payload, "expected_recognition": expected}
            anomaly = self.artifacts.record_anomaly(
                content_code,
                f"播报窗口 {assigned_id} 的识别原始结果与当前播报语料期望不一致。",
                **mismatch_payload,
            )
            self.append_jsonl("broadcast_recognition_anomalies.jsonl", {**anomaly, **mismatch_payload})
        if assigned_id and not expected:
            missing_code = "MISSING_EXPECTED_RECOGNITION_CONTRACT"
            missing_payload = {**payload, "expected_recognition": expected}
            anomaly = self.artifacts.record_anomaly(
                missing_code,
                f"播报窗口 {assigned_id} 未声明当前语料的期望原始识别字段。",
                **missing_payload,
            )
            self.append_jsonl("broadcast_recognition_anomalies.jsonl", {**anomaly, **missing_payload})
        if anomaly_codes:
            return {
                "status": "FAIL",
                "reason": anomaly_codes[0],
                "anomaly_codes": anomaly_codes,
                "expected_recognition": expected,
                **payload,
            }
        self.artifacts.emit(
            "RECOGNITION_ASSOCIATED",
            message=f"识别结果已关联至播报窗口 {assigned_id}。",
            task_log=True,
            expected_recognition=expected,
            **payload,
        )
        return {
            "status": "PASS",
            "reason": "ONE_TO_ONE_BROADCAST_RECOGNITION",
            "expected_recognition": expected,
            **payload,
        }

    def record_recognition(
        self,
        raw_values: Mapping[str, Any],
        *,
        source: str,
        case_id: str = "",
        broadcast_id: str | None = None,
        normalized: Mapping[str, Any] | None = None,
        judgement: ToolJudgement | None = None,
        evidence_refs: Iterable[str] = (),
        suppress_content_mismatch: bool = False,
    ) -> dict[str, Any]:
        """Record raw device recognition before any project-side normalization.

        ``raw_values`` is the literal algorithm or cloud output.  In
        particular, an offline result such as ``ni3 hao3 kong1 tiao2`` must be
        supplied unchanged for ``keyword``/``intent``.  Normalized text is
        stored separately and can never overwrite the raw tag value.
        """
        source_name = str(source or "").strip().lower()
        if source_name not in {"offline", "online"}:
            raise ValueError("recognition source must be offline or online")
        raw_payload = dict(raw_values)
        if not raw_payload:
            raise ValueError("raw recognition values cannot be empty")
        refs = [str(item) for item in evidence_refs]
        evidence = refs[0] if refs else None
        channel = f"{source_name.upper()}_RECOGNITION"
        tags = [
            RawTag(name, value, source="device", evidence=evidence)
            for name, value in raw_payload.items()
        ]
        event_fields = self.artifacts.emit_observation(
            channel,
            tags,
            event="RAW_RECOGNITION",
            normalized=dict(normalized or {}),
            judgement=judgement,
            case_id=case_id,
            recognition_source=source_name,
            evidence_refs=refs,
        )
        tool_entry = {
            "recorded_at": time.time(),
            "channel": channel,
            "case_id": case_id,
            "broadcast_id": broadcast_id or "",
            "recognition_source": source_name,
            "raw": raw_payload,
            "normalized": dict(normalized or {}),
            "evidence_refs": refs,
        }
        # This is deliberately separate from task.log: testers can inspect the
        # literal algorithm form without reading converted business fields.
        raw_log = self.append_jsonl("recognition_raw.log", tool_entry)
        association = self.observe_recognition(
            raw_payload,
            case_id=case_id,
            broadcast_id=broadcast_id,
            evidence_refs=refs,
            suppress_content_mismatch=suppress_content_mismatch,
        )
        if case_id:
            self._recognition_associations.setdefault(case_id, []).append(dict(association))
        return {
            "raw_recognition_log": str(raw_log),
            "observation": event_fields,
            "association": association,
            "status": association["status"],
            "reason": association["reason"],
        }

    def record_wakeup(
        self,
        wake_word: Mapping[str, Any],
        raw_values: Mapping[str, Any],
        *,
        case_id: str = "",
        broadcast_id: str | None = None,
        evidence_refs: Iterable[str] = (),
        duration_ms: int | None = None,
    ) -> dict[str, Any]:
        """Confirm one explicitly selected wake word against its raw device output.

        Callers select the next profile/requirements-table item in order and
        pass it here.  A generic wake-up marker is not sufficient when a
        device has several active wake words.
        """
        requirement = dict(wake_word)
        wake_word_id = str(requirement.get("wake_word_id") or "").strip()
        spoken_text = str(requirement.get("spoken_text") or "").strip()
        expected_raw = dict(requirement.get("expected_raw") or {})
        if not wake_word_id or not spoken_text or not expected_raw:
            raise ValueError("wake word requires wake_word_id, spoken_text, and expected_raw")
        expected_current: dict[str, Any] = {}
        if self.wake_word_sequence.requirements:
            try:
                expected_current = self.current_wake_word()
                order_ok = self.wake_word_sequence.matches_current(wake_word_id)
            except RuntimeError:
                # A device reporting another wake-up after all rows have been
                # verified is still a test anomaly, not an uncaught tool error.
                order_ok = False
        else:
            order_ok = True
        if broadcast_id and broadcast_id in self.broadcast_tracker.broadcasts:
            self.broadcast_tracker.broadcasts[broadcast_id]["expected_recognition"] = expected_raw
        observed = dict(raw_values)
        expected_keyword = expected_raw.get("keyword")
        actual_keyword = observed.get("keyword")
        exact_match = all(observed.get(name) == expected for name, expected in expected_raw.items())
        normalized, judgement = judge_wakeup(
            str(actual_keyword) if actual_keyword is not None else None,
            str(expected_keyword) if expected_keyword is not None else None,
            final_observed=bool(observed), duration_ms=duration_ms, evidence_refs=tuple(str(item) for item in evidence_refs),
        )
        if not exact_match and judgement.tool_status == "PASS":
            normalized = {**normalized, "wakeup_status": "FAIL", "active_wake_word_id": wake_word_id}
            judgement = ToolJudgement(
                "FAIL", "EXACT_MATCH", "wakeup_word_raw_output_mismatch",
                expected=expected_raw, actual=observed, duration_ms=duration_ms,
                evidence_refs=tuple(str(item) for item in evidence_refs),
            )
        else:
            normalized = {**normalized, "active_wake_word_id": wake_word_id, "spoken_wake_word": spoken_text}
        if not order_ok:
            normalized = {**normalized, "wakeup_status": "FAIL"}
            judgement = ToolJudgement(
                "FAIL", "ORDERED_EXACT_MATCH", "wake_word_order_violation",
                expected=expected_current, actual=requirement, duration_ms=duration_ms,
                evidence_refs=tuple(str(item) for item in evidence_refs),
            )
        recognition = self.record_recognition(
            observed,
            source="offline",
            case_id=case_id,
            broadcast_id=broadcast_id,
            normalized=normalized,
            judgement=judgement,
            evidence_refs=evidence_refs,
            suppress_content_mismatch=True,
        )
        association = dict(recognition["association"])
        wake_payload = {
            "wake_word_id": wake_word_id,
            "spoken_text": spoken_text,
            "expected_raw": expected_raw,
            "raw_values": observed,
            "wakeup_status": judgement.tool_status,
            "recognition": recognition,
        }
        association_reason = str(association.get("reason") or "")
        if association_reason == "UNEXPECTED_RECOGNITION":
            anomaly = self.artifacts.record_anomaly(
                "WAKE_WORD_WITHOUT_PLAYBACK",
                f"唤醒词 {wake_word_id} 在没有可关联播报的情况下出现。",
                case_id=case_id, broadcast_id=broadcast_id or "", **wake_payload,
            )
            wake_payload.setdefault("anomaly_codes", []).append(anomaly["code"])
            if case_id:
                self._recognition_associations.setdefault(case_id, []).append({
                    "status": "FAIL",
                    "reason": anomaly["code"],
                    "broadcast_id": broadcast_id or "",
                    "wake_word_id": wake_word_id,
                })
        elif association_reason == "MULTIPLE_RECOGNITIONS_FOR_PLAYBACK":
            anomaly = self.artifacts.record_anomaly(
                "WAKE_WORD_MULTIPLE_RESULTS_FOR_PLAYBACK",
                f"播报窗口 {association.get('broadcast_id', '')} 出现第 {association.get('recognition_count', 0)} 个唤醒词结果。",
                case_id=case_id, broadcast_id=str(association.get("broadcast_id") or broadcast_id or ""),
                **wake_payload,
            )
            wake_payload.setdefault("anomaly_codes", []).append(anomaly["code"])
            if case_id:
                self._recognition_associations.setdefault(case_id, []).append({
                    "status": "FAIL",
                    "reason": anomaly["code"],
                    "broadcast_id": str(association.get("broadcast_id") or broadcast_id or ""),
                    "wake_word_id": wake_word_id,
                })
        if order_ok and judgement.tool_status == "PASS":
            self.wake_word_sequence.advance()
        if not order_ok:
            anomaly = self.artifacts.record_anomaly(
                "WAKE_WORD_ORDER_VIOLATION",
                f"当前应验证唤醒词 {expected_current.get('wake_word_id', '')}，却提交了 {wake_word_id}。",
                case_id=case_id,
                broadcast_id=broadcast_id or "",
                expected_wake_word=expected_current,
                actual_wake_word=requirement,
            )
            if case_id:
                self._recognition_associations.setdefault(case_id, []).append({
                    "status": "FAIL",
                    "reason": anomaly["code"],
                    "broadcast_id": broadcast_id or "",
                    "wake_word_id": wake_word_id,
                })
        elif judgement.tool_status != "PASS":
            anomaly = self.artifacts.record_anomaly(
                "WAKE_WORD_MISMATCH",
                f"当前唤醒词 {wake_word_id} 的设备原始结果与需求表不一致。",
                case_id=case_id, broadcast_id=broadcast_id or "", **wake_payload,
            )
            if case_id:
                self._recognition_associations.setdefault(case_id, []).append({
                    "status": "FAIL",
                    "reason": anomaly["code"],
                    "broadcast_id": broadcast_id or "",
                    "wake_word_id": wake_word_id,
                })
        self.append_jsonl("wake_word_verification.log", wake_payload)
        return wake_payload

    def apply_recognition_contract(self, result: CaseResult, associations: Iterable[Mapping[str, Any]]) -> CaseResult:
        """Upgrade an otherwise successful case to FAIL when its association is abnormal."""
        values = [dict(item) for item in associations]
        codes = [str(item.get("reason")) for item in values if item.get("status") != "PASS"]
        if not codes:
            return result
        status = result.reviewed_status or result.raw_status
        if status in {"PASS", "EXPECTED"}:
            result.reviewed_status = "FAIL"
            result.reason = "; ".join(filter(None, [result.reason, *codes]))
        result.facts = {**result.facts, "broadcast_recognition_associations": values}
        return result

    def wait_observation_window(
        self,
        seconds: float,
        *,
        fetch: Callable[[], tuple[list[Any], bool]],
        parse: Callable[[list[Any]], list[Any]],
        stop_reason: Callable[[], str | None],
        predicate: Callable[[list[Any]], bool] | None = None,
        settle_seconds: float = 0.75,
    ) -> tuple[list[Any], list[Any], str | None, bool]:
        """在有界观察窗口内轮询事件，避免项目层重复实现等待逻辑。"""
        deadline = time.monotonic() + max(0.0, float(seconds))
        events: list[Any] = []
        markers: list[Any] = []
        complete = True
        while time.monotonic() < deadline:
            reason = stop_reason()
            events, complete = fetch()
            markers = parse(events)
            if predicate and predicate(markers):
                settle_deadline = min(deadline, time.monotonic() + max(0.0, settle_seconds))
                while time.monotonic() < settle_deadline and not stop_reason():
                    time.sleep(min(0.1, max(0.0, settle_deadline - time.monotonic())))
                events, complete = fetch()
                return events, parse(events), stop_reason(), complete
            if reason:
                return events, markers, reason, complete
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        events, complete = fetch()
        return events, parse(events), stop_reason(), complete

    def append_jsonl(self, name: str, payload: dict[str, Any]) -> Path:
        """增量保存公共工具计时/关联证据。"""
        safe_name = Path(name).name
        path = self.artifacts.run_dir / "tool_logs" / safe_name
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        return path

    def write_evidence(
        self,
        events: Iterable[Any],
        *,
        unit: int,
        case_id: str,
        phase: str,
        attempt: int,
    ) -> tuple[Path, str]:
        """以独立用例证据文件保存观察窗口，不创建合并串口日志。"""
        safe_case = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(case_id)).strip("_") or "case"
        path = self.artifacts.run_dir / "evidence" / f"{unit:06d}_{safe_case}_{phase}_{attempt}.log"
        lines: list[str] = []
        for event in events:
            timestamp = getattr(event, "timestamp", "")
            port = getattr(event, "port", "")
            role = getattr(event, "role", "")
            line = getattr(event, "line", str(event))
            lines.append(f"{timestamp} [{port}/{role}] {line}\n")
        path.write_text("".join(lines), encoding="utf-8")
        return path, sha256_file(path)

    def record_case(self, row: dict[str, Any] | CaseResult) -> None:
        """统一交给任务产物对象增量写入 CSV/状态计数。"""
        if isinstance(row, CaseResult) and hasattr(self.artifacts, "record_case"):
            associations = [
                *list(row.facts.get("broadcast_recognition_associations", ())),
                *self._recognition_associations.pop(row.case_id, []),
            ]
            if associations:
                row = self.apply_recognition_contract(row, associations)
            self.artifacts.record_case(row)
            return
        if hasattr(self.artifacts, "add_row"):
            self.artifacts.add_row(row)  # type: ignore[arg-type]
            return
        raise TypeError("artifacts must implement record_case or add_row")
