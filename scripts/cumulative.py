"""持续任务的累计账本：跨 run 承接历史统计，禁止清零。

场景：OTA 持续监控、长压测。每个 run 只是一个「窗口」，累计指标必须写在
任务级 `work/cumulative.json`，由窗口结束时 merge，而不是跟着 run 目录一起换新。

用法：
    from lstest.scripts import cumulative
    cum = cumulative.load(work_dir, "NOAH-413")          # 无则返回空账本
    cum = cumulative.merge_window(cum, window_record)     # 窗口结束合并
    cumulative.save(work_dir, cum)
    print(cumulative.render_line(cum, window=window_record))
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "lstest.cumulative.v1"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def path_of(work_dir: Path) -> Path:
    return Path(work_dir) / "cumulative.json"


def empty(task_id: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "task_id": task_id,
        "created_at": _now(),
        "updated_at": _now(),
        "windows": 0,
        "first_window_at": None,
        "last_window_at": None,
        "total_seconds": 0.0,
        "total_serial_bytes": 0,
        "counters": {},
        "anomalies": 0,
        "per_window": [],
    }


def load(work_dir: Path, task_id: str) -> dict[str, Any]:
    path = path_of(work_dir)
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema") == SCHEMA:
                return payload
        except (OSError, ValueError):
            pass
    return empty(task_id)


def save(work_dir: Path, payload: dict[str, Any]) -> Path:
    path = path_of(work_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = _now()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def merge_window(payload: dict[str, Any], window: Mapping[str, Any]) -> dict[str, Any]:
    """把本轮窗口并入累计；同一 run 重复合并按替换处理，不重复累加。"""
    payload = json.loads(json.dumps(payload))  # 深拷贝，避免调用方引用被改
    run = str(window.get("run") or "")
    per_window = [w for w in payload.get("per_window") or [] if str(w.get("run")) != run]
    per_window.append({
        "run": run,
        "started_at": window.get("started_at"),
        "ended_at": window.get("ended_at"),
        "seconds": float(window.get("seconds") or 0.0),
        "serial_bytes": int(window.get("serial_bytes") or 0),
        "marks": int(window.get("marks") or 0),
        "counters": dict(window.get("counters") or {}),
        "anomalies": int(window.get("anomalies") or 0),
    })
    per_window.sort(key=lambda w: str(w.get("started_at") or ""))
    payload["per_window"] = per_window
    payload["windows"] = len(per_window)
    payload["total_seconds"] = round(sum(float(w["seconds"]) for w in per_window), 1)
    payload["total_serial_bytes"] = sum(int(w["serial_bytes"]) for w in per_window)
    payload["anomalies"] = sum(int(w["anomalies"]) for w in per_window)
    counters: dict[str, int] = {}
    for item in per_window:
        for code, count in (item.get("counters") or {}).items():
            counters[code] = counters.get(code, 0) + int(count)
    payload["counters"] = dict(sorted(counters.items(), key=lambda kv: -kv[1]))
    payload["first_window_at"] = per_window[0].get("started_at") if per_window else None
    payload["last_window_at"] = per_window[-1].get("ended_at") if per_window else None
    return payload


def render_line(payload: dict[str, Any], window: Mapping[str, Any] | None = None) -> str:
    """生成写进 tool.log 的累计行：历史累计 + 本轮增量，一眼看出没清零。"""
    total_hours = round(float(payload.get("total_seconds") or 0) / 3600, 1)
    serial_gb = round(float(payload.get("total_serial_bytes") or 0) / (1024 ** 3), 2)
    parts = [
        f"累计 窗口={payload.get('windows', 0)}",
        f"总时长={total_hours}h",
        f"串口={serial_gb}GB",
        f"异常累计={payload.get('anomalies', 0)}",
        f"首窗={payload.get('first_window_at') or '-'}",
        f"末窗={payload.get('last_window_at') or '-'}",
    ]
    if window is not None:
        delta_hours = round(float(window.get("seconds") or 0) / 3600, 2)
        parts.append(f"本轮增量={delta_hours}h/{(window.get('marks') or 0)}标记")
    top = list((payload.get("counters") or {}).items())[:5]
    if top:
        parts.append("主要标记=" + " ".join(f"{code}x{count}" for code, count in top))
    return "；".join(parts)


def covered_since(payload: dict[str, Any], since: str) -> bool:
    """累计账本是否已覆盖到指定时间（用于门禁判断本轮是否已被并入）。"""
    last = str(payload.get("last_window_at") or "")
    return bool(last) and last >= str(since)
