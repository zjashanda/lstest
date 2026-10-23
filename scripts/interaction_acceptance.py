"""单轮交互结果与验收模式。"""

from __future__ import annotations

from typing import Iterable

try:
    from .verdict import Verdict, VerdictLayerResult, VerdictStatus, aggregate_verdict
except ImportError:  # direct execution fallback
    from verdict import Verdict, VerdictLayerResult, VerdictStatus, aggregate_verdict  # type: ignore

OUTCOMES = ("hit", "misheard", "empty", "asleep")

OUTCOME_LABELS = {
    "hit": "唤醒+识别正确",
    "misheard": "唤醒+误识别",
    "empty": "唤醒+无识别结果",
    "asleep": "未唤醒",
}

MODES = {
    "wake_only": {
        "label": "仅唤醒验收",
        "wake_playback": False,
        "asr_result": "ignored",
        "asr_playback": False,
        "require_any_asr": False,
    },
    "wake_playback": {
        "label": "唤醒+唤醒播报验收",
        "wake_playback": True,
        "asr_result": "ignored",
        "asr_playback": False,
        "require_any_asr": False,
    },
    "strict": {
        "label": "严格词表验收",
        "wake_playback": False,
        "asr_result": "strict",
        "asr_playback": False,
        "require_any_asr": True,
        "reject_multi_result": True,
    },
    "strict_playback": {
        "label": "严格词表+播报验收",
        "wake_playback": True,
        "asr_result": "strict",
        "asr_playback": True,
        "require_any_asr": True,
        "reject_multi_result": True,
    },
    "any_asr": {
        "label": "任意识别验收",
        "wake_playback": False,
        "asr_result": "any",
        "asr_playback": False,
        "require_any_asr": True,
    },
    "any_asr_playback": {
        "label": "任意识别+播报验收",
        "wake_playback": True,
        "asr_result": "any",
        "asr_playback": True,
        "require_any_asr": True,
    },
    "asr_optional": {
        "label": "识别容错验收",
        "wake_playback": False,
        "asr_result": "optional",
        "asr_playback": False,
        "require_any_asr": True,
    },
    "asr_optional_playback": {
        "label": "识别容错+播报验收",
        "wake_playback": True,
        "asr_result": "optional",
        "asr_playback": True,
        "require_any_asr": True,
    },
}


def mode_label(mode: str) -> str:
    return str(MODES.get(mode, {}).get("label", mode))


def evaluate_round(
    outcome: str,
    mode: str,
    *,
    wake_playback_count: int = 0,
    wake_playback_complete: bool = False,
    asr_response_count: int = 0,
    asr_playback_count: int = 0,
    asr_playback_complete: bool = False,
    multi_result: bool = False,
) -> tuple[bool, str]:
    """按单轮交互结果和播报旁证判断本轮是否通过。"""
    config = MODES.get(mode)
    if config is None:
        return False, f"未知验收模式: {mode}"
    if outcome not in OUTCOMES:
        return False, f"未知交互结果: {outcome}"
    if outcome == "asleep":
        return False, "未唤醒"
    if config.get("reject_multi_result") and multi_result:
        return False, "单轮出现多个唤醒或识别结果"

    result_mode = config["asr_result"]
    if result_mode == "strict" and outcome != "hit":
        return False, f"要求 hit，实际 {OUTCOME_LABELS[outcome]}"
    if result_mode == "any" and outcome not in {"hit", "misheard"}:
        return False, f"要求有识别结果，实际 {OUTCOME_LABELS[outcome]}"

    if config["wake_playback"]:
        if wake_playback_count < 1:
            return False, "未观察到唤醒播报"
        if not wake_playback_complete:
            return False, "唤醒播报缺少完成状态"

    if config["asr_playback"] and outcome in {"hit", "misheard"}:
        if asr_response_count < 1:
            return False, "未观察到识别响应"
        if asr_playback_count < 1:
            return False, "未观察到识别播报"
        if not asr_playback_complete:
            return False, "识别播报缺少完成状态"

    return True, f"{OUTCOME_LABELS[outcome]} / {mode_label(mode)}"


def evaluate_run(outcomes: Iterable[str], mode: str) -> tuple[bool, str]:
    """检查任务级约束；单轮判断仍由 evaluate_round 完成。"""
    config = MODES.get(mode)
    if config is None:
        return False, f"未知验收模式: {mode}"
    values = list(outcomes)
    if not values:
        return False, "没有交互结果"
    if config.get("require_any_asr") and mode in {"asr_optional", "asr_optional_playback"}:
        if not any(item in {"hit", "misheard"} for item in values):
            return False, "ASR 全程没有识别结果"
    return True, "整轮任务验收通过"


def _round_anomaly_codes(
    outcome: str,
    mode: str,
    *,
    wake_playback_count: int,
    wake_playback_complete: bool,
    asr_response_count: int,
    asr_playback_count: int,
    asr_playback_complete: bool,
    multi_result: bool,
) -> tuple[str, ...]:
    config = MODES.get(mode, {})
    codes: list[str] = []
    if outcome == "asleep":
        codes.append("ASLEEP")
    if multi_result and config.get("reject_multi_result"):
        codes.append("MULTI_RESULT")
    if config.get("wake_playback"):
        if wake_playback_count < 1:
            codes.append("WAKE_PLAYBACK_MISSING")
        elif not wake_playback_complete:
            codes.append("WAKE_PLAYBACK_INCOMPLETE")
    if config.get("asr_playback") and outcome in {"hit", "misheard"}:
        if asr_response_count < 1:
            codes.append("ASR_RESPONSE_MISSING")
        elif asr_playback_count < 1:
            codes.append("ASR_PLAYBACK_MISSING")
        elif not asr_playback_complete:
            codes.append("ASR_PLAYBACK_INCOMPLETE")
    return tuple(dict.fromkeys(codes))


def round_verdict(
    outcome: str,
    mode: str,
    *,
    wake_playback_count: int = 0,
    wake_playback_complete: bool = False,
    asr_response_count: int = 0,
    asr_playback_count: int = 0,
    asr_playback_complete: bool = False,
    multi_result: bool = False,
) -> Verdict:
    """Convert one acceptance-mode decision into the framework verdict model."""
    ok, reason = evaluate_round(
        outcome,
        mode,
        wake_playback_count=wake_playback_count,
        wake_playback_complete=wake_playback_complete,
        asr_response_count=asr_response_count,
        asr_playback_count=asr_playback_count,
        asr_playback_complete=asr_playback_complete,
        multi_result=multi_result,
    )
    layer = VerdictLayerResult(
        "functional",
        VerdictStatus.PASS if ok else VerdictStatus.FAIL,
        True,
        "test-design",
        reason,
    )
    codes = _round_anomaly_codes(
        outcome,
        mode,
        wake_playback_count=wake_playback_count,
        wake_playback_complete=wake_playback_complete,
        asr_response_count=asr_response_count,
        asr_playback_count=asr_playback_count,
        asr_playback_complete=asr_playback_complete,
        multi_result=multi_result,
    )
    return aggregate_verdict([layer], anomaly_codes=codes, primary_reason=reason)


def run_verdict(outcomes: Iterable[str], mode: str) -> Verdict:
    """Convert task-level acceptance constraints into a verdict."""
    values = list(outcomes)
    ok, reason = evaluate_run(values, mode)
    layer = VerdictLayerResult(
        "functional",
        VerdictStatus.PASS if ok else VerdictStatus.FAIL,
        True,
        "test-design",
        reason,
    )
    codes: list[str] = []
    if not values:
        codes.append("NO_INTERACTION_RESULTS")
    elif mode in {"asr_optional", "asr_optional_playback"} and not any(
        item in {"hit", "misheard"} for item in values
    ):
        codes.append("ASR_OPTIONAL_EMPTY")
    return aggregate_verdict([layer], anomaly_codes=codes, primary_reason=reason)
