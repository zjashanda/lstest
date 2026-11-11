"""Declarative case order and playback actions for venusws63 (no-CP topology).

This module is *data*: it lists which case runs first, and what the host must
play into the device for each of them.  It deliberately contains **no expected
verdicts** and imports only the standard library, so a case plan can never make
a case pass.

Topology: venusws63 has no dedicated CP serial port.  Only ``ap`` (COM14) and
``upper`` (COM13) observe device logs; ``asr`` is a legacy alias of ``upper`` on
the same physical port, and ``control`` (COM15) is the power/PA port.  Playback
therefore always targets the device microphone and is verified through the two
observation ports, never through a CP reader.

Utterance files are *declared paths* under the project's audio directory.  They
are produced by the project's TTS step (``lstest tts``); the repository ships no
audio binaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

__all__ = [
    "PROJECT",
    "TOPOLOGY",
    "OBSERVATION_ROLES",
    "UTTERANCE_DIR",
    "PlaybackAction",
    "CaseStep",
    "CASE_PLAN",
    "iter_case_ids",
    "playback_for",
    "to_dict",
]

PROJECT = "venusws63"
#: Same value as ``profiles/venusws63/profile.json`` -> ``notes.topology``.
TOPOLOGY = "ap_wifi_control_no_cp"
#: Roles whose serial logs may carry device evidence; ``asr`` aliases ``upper``.
OBSERVATION_ROLES = ("ap", "upper")
#: Directory (relative to the project skill root) holding synthesized utterances.
UTTERANCE_DIR = "audio/utterances"


@dataclass(frozen=True)
class PlaybackAction:
    """One host playback action: a synthesized utterance played N times."""

    utterance: str
    repeat: int = 1
    gap_seconds: float = 2.0
    observe_seconds: float = 15.0
    #: Where the audio is injected.  WS63 has no CP reader; audio always goes
    #: into the device microphone from the host.
    target: str = "device_mic"
    notes: str = ""


@dataclass(frozen=True)
class CaseStep:
    """One case and the playback actions that drive it, in execution order."""

    case_id: str
    stage: str
    playback: tuple[PlaybackAction, ...]
    observe_seconds: float = 15.0
    notes: tuple[str, ...] = ()


def _utterance(slug: str) -> str:
    return f"{UTTERANCE_DIR}/{slug}.wav"


#: Ordered plan.  Order follows the Polaris WS63 validation sequence: coverage
#: sweep first, then the negative TTS-URL attribution regression, then stress.
CASE_PLAN: tuple[CaseStep, ...] = (
    CaseStep(
        case_id="polaris-task-ws63-com20-coverage-check",
        stage="command",
        observe_seconds=15.0,
        playback=(
            PlaybackAction(utterance=_utterance("xiao-mei-xiao-mei"), repeat=2),
            PlaybackAction(utterance=_utterance("da-kai-kong-tiao"), repeat=2),
            PlaybackAction(utterance=_utterance("guan-bi-kong-tiao"), repeat=2),
            PlaybackAction(utterance=_utterance("kong-tiao-mo-shi-zhi-leng"), repeat=2),
            PlaybackAction(utterance=_utterance("kong-tiao-wen-du-duo-shao"), repeat=2),
            PlaybackAction(utterance=_utterance("yin-liang-tiao-da"), repeat=2),
            PlaybackAction(utterance=_utterance("bo-fang-xin-wen"), repeat=2),
            PlaybackAction(utterance=_utterance("bo-fang-yin-yue"), repeat=2),
        ),
        notes=(
            "覆盖补验证：每类指令只回放一次语料（repeat 表示同一文件的重复次数）。",
            "唤醒语料单独重复 2 次，用于覆盖首次唤醒与在线唤醒两条观测口。",
        ),
    ),
    CaseStep(
        case_id="polaris-task-ws63-tts-empty-url-attribution",
        stage="attribution",
        observe_seconds=20.0,
        playback=(
            PlaybackAction(utterance=_utterance("da-kai-kong-tiao"), repeat=3, gap_seconds=3.0),
        ),
        notes=(
            "DeviceControl 负向归因回归：同一指令重复 3 次，用于观察是否有回复但 TTS 地址为空。",
            "无 CP 口，只从 ap/upper 两个观测口取证据。",
        ),
    ),
    CaseStep(
        case_id="polaris-task-online-mixed-stress-ws63",
        stage="network",
        observe_seconds=30.0,
        playback=(
            PlaybackAction(utterance=_utterance("xiao-mei-xiao-mei"), repeat=10, gap_seconds=6.0),
            PlaybackAction(utterance=_utterance("bo-fang-yin-yue"), repeat=10, gap_seconds=8.0),
            PlaybackAction(utterance=_utterance("bo-fang-xin-wen"), repeat=5, gap_seconds=8.0),
            PlaybackAction(utterance=_utterance("kong-tiao-wen-du-duo-shao"), repeat=5, gap_seconds=6.0),
        ),
        notes=(
            "混合压测：唤醒、音乐、新闻、问答交替回放。",
            "轮次与间隔只是播放计划；通过/失败由 cases/ 的断言和 profile 事实决定。",
        ),
    ),
)


def iter_case_ids() -> Iterator[str]:
    """Yield case ids in execution order."""
    for step in CASE_PLAN:
        yield step.case_id


def playback_for(case_id: str) -> tuple[PlaybackAction, ...]:
    """Return the playback actions declared for ``case_id``."""
    for step in CASE_PLAN:
        if step.case_id == case_id:
            return step.playback
    raise KeyError(f"{PROJECT}: unknown case_id {case_id!r}")


def to_dict() -> dict[str, Any]:
    """Serialize the plan (data only, no expectations)."""
    return {
        "schema": "lstest.case_plan.v1",
        "project": PROJECT,
        "topology": TOPOLOGY,
        "observation_roles": list(OBSERVATION_ROLES),
        "case_order": list(iter_case_ids()),
        "playback_total": sum(action.repeat for step in CASE_PLAN for action in step.playback),
        "steps": [
            {
                "case_id": step.case_id,
                "stage": step.stage,
                "observe_seconds": step.observe_seconds,
                "playback": [
                    {
                        "utterance": action.utterance,
                        "repeat": action.repeat,
                        "gap_seconds": action.gap_seconds,
                        "observe_seconds": action.observe_seconds,
                        "target": action.target,
                    }
                    for action in step.playback
                ],
                "notes": list(step.notes),
            }
            for step in CASE_PLAN
        ],
    }
