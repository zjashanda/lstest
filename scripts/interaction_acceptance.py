"""单轮交互结果与验收模式。"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

OUTCOMES = ("hit", "misheard", "empty", "asleep")

DEFAULT_CAPABILITIES = {
    "wake_playback": True,
    "asr_response": True,
    "asr_playback": True,
    "protocol_frame": True,
}


def capabilities_of(profile: Mapping[str, Any] | None) -> dict[str, bool]:
    """机型能力：未声明的按支持处理，声明的按声明值。"""
    caps = dict(DEFAULT_CAPABILITIES)
    if not isinstance(profile, Mapping):
        return caps
    declared = profile.get("capabilities")
    if not isinstance(declared, Mapping):
        declared = {key: profile[key] for key in DEFAULT_CAPABILITIES if key in profile}
    for key, value in declared.items():
        if key in caps:
            caps[key] = bool(value)
    return caps

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


def rejects_multi_result(mode: str) -> bool:
    """只有显式声明 reject_multi_result 的模式才把多结果判为失败。"""
    return bool(MODES.get(mode, {}).get("reject_multi_result", False))


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
    capabilities: Mapping[str, Any] | None = None,
) -> tuple[bool, str]:
    """按单轮交互结果和播报旁证判断本轮是否通过。"""
    config = MODES.get(mode)
    if config is None:
        return False, f"未知验收模式: {mode}"
    caps = capabilities_of(capabilities)
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

    if config["wake_playback"] and caps["wake_playback"]:
        if wake_playback_count < 1:
            return False, "未观察到唤醒播报"
        if not wake_playback_complete:
            return False, "唤醒播报缺少完成状态"

    if config["asr_playback"] and outcome in {"hit", "misheard"}:
        if caps["asr_response"] and asr_response_count < 1:
            return False, "未观察到识别响应"
        if caps["asr_playback"] and asr_playback_count < 1:
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

def baseline_missing(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    """横向基线：同类用例普遍具备的要素，个别缺失才算异常。

    rows: [{"case_id": "G9", "features": {"wake_result": True, "protocol_frame": False}}]
    返回可直接写入 tool.log 的说明文字。
    """
    items = [row for row in rows if isinstance(row, Mapping)]
    if len(items) < 2:
        return []
    features: dict[str, int] = {}
    for row in items:
        for name, present in (row.get("features") or {}).items():
            features[name] = features.get(name, 0) + (1 if present else 0)
    findings: list[str] = []
    total = len(items)
    for name, hits in features.items():
        if hits == 0 or hits == total:
            continue
        for row in items:
            if not (row.get("features") or {}).get(name):
                case_id = row.get("case_id") or "?"
                findings.append(f"{case_id} 缺少 {name}（同类 {hits}/{total} 用例有）")
    return findings
