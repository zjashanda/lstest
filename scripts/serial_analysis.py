"""Serial log analysis for a completed monitor window.

Reads the ``serial_logs/serial_<port>_<role>.log`` files written by
:class:`lstest.scripts.serial_capture.SerialManager` and produces per-port
facts plus an OTA verdict that is explicit about missing evidence.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

LINE_RE = re.compile(r"^\[(?P<ts>[^\]]+)\] (?P<port>\S+) (?P<role>\S+) #(?P<n>\d+) (?P<text>.*)$")

OTA_TERMS = ("ota", "upgrade", "download", "image", "firmware", "verify", "switch", "reboot")
OTA_SUCCESS = ("ota success", "upgrade success", "switch success", "verify success", "ota complete")
OTA_FAILURE = ("ota fail", "upgrade fail", "verify fail", "switch fail", "download fail", "ota error")
NETWORK_TERMS = ("wifi connected", "wlan", "haier clound", "cloud now reconnect", "ip:", "mqtt")
REBOOT_TERMS = ("bootloader application", "boot reason", "reset", "reboot", "venus ap appliction")

GAP_THRESHOLD_S = 120.0


def _parse_ts(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _classify_ota(text: str) -> str:
    lowered = text.lower()
    if any(term in lowered for term in OTA_FAILURE):
        return "fail"
    if any(term in lowered for term in OTA_SUCCESS):
        return "success"
    return "mention"


def analyze_port(path: Path) -> Dict[str, Any]:
    lines = 0
    first_ts = ""
    last_ts = ""
    prev = None
    max_gap_s = 0.0
    max_gap_at = ""
    gap_count = 0
    ota_hits: List[Dict[str, str]] = []
    ota_verdicts: Dict[str, int] = {"success": 0, "fail": 0, "mention": 0}
    network_hits = 0
    reboot_hits = 0
    sample_fail: List[str] = []

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            match = LINE_RE.match(raw.rstrip("\n"))
            if not match:
                continue
            lines += 1
            text = match.group("text")
            stamp = _parse_ts(match.group("ts"))
            if stamp is not None:
                first_ts = first_ts or match.group("ts")
                last_ts = match.group("ts")
                if prev is not None:
                    delta = (stamp - prev).total_seconds()
                    if delta > max_gap_s:
                        max_gap_s = delta
                        max_gap_at = match.group("ts")
                    if delta > GAP_THRESHOLD_S:
                        gap_count += 1
                prev = stamp
            lowered = text.lower()
            if any(term in lowered for term in OTA_TERMS):
                kind = _classify_ota(text)
                ota_verdicts[kind] += 1
                if len(ota_hits) < 40:
                    ota_hits.append({"at": match.group("ts"), "kind": kind, "text": text[:300]})
                if kind == "fail" and len(sample_fail) < 10:
                    sample_fail.append(f"{match.group('ts')} {text[:240]}")
            if any(term in lowered for term in NETWORK_TERMS):
                network_hits += 1
            if any(term in lowered for term in REBOOT_TERMS):
                reboot_hits += 1

    return {
        "file": path.name,
        "bytes": path.stat().st_size,
        "lines": lines,
        "first_line_at": first_ts,
        "last_line_at": last_ts,
        "max_gap_s": round(max_gap_s, 1),
        "max_gap_at": max_gap_at,
        "gaps_over_threshold": gap_count,
        "gap_threshold_s": GAP_THRESHOLD_S,
        "ota_mentions": ota_verdicts["mention"],
        "ota_success_markers": ota_verdicts["success"],
        "ota_failure_markers": ota_verdicts["fail"],
        "network_markers": network_hits,
        "reboot_markers": reboot_hits,
        "ota_samples": ota_hits,
        "failure_samples": sample_fail,
    }


def analyze_run(run_dir: Path, session: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    run_dir = Path(run_dir)
    ports: Dict[str, Any] = {}
    serial_root = run_dir / "serial_logs"
    if serial_root.is_dir():
        for path in sorted(serial_root.glob("*.log")):
            ports[path.name] = analyze_port(path)

    totals = {
        "ports": len(ports),
        "bytes": sum(item["bytes"] for item in ports.values()),
        "lines": sum(item["lines"] for item in ports.values()),
        "ota_success_markers": sum(item["ota_success_markers"] for item in ports.values()),
        "ota_failure_markers": sum(item["ota_failure_markers"] for item in ports.values()),
        "ota_mentions": sum(item["ota_mentions"] for item in ports.values()),
        "network_markers": sum(item["network_markers"] for item in ports.values()),
        "reboot_markers": sum(item["reboot_markers"] for item in ports.values()),
        "ports_with_data": sum(1 for item in ports.values() if item["lines"] > 0),
        "gaps_over_threshold": sum(item["gaps_over_threshold"] for item in ports.values()),
    }

    if totals["ota_success_markers"] > 0 and totals["ota_failure_markers"] == 0:
        ota_status = "OBSERVED_SUCCESS"
    elif totals["ota_failure_markers"] > 0:
        ota_status = "OBSERVED_FAILURE"
    elif totals["ota_mentions"] > 0:
        ota_status = "OBSERVED_NO_VERDICT"
    else:
        ota_status = "NOT_OBSERVED"

    return {
        "analyzed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "run_dir": str(run_dir),
        "session": session or {},
        "totals": totals,
        "ota_status": ota_status,
        "ports": ports,
    }


def render_markdown(report: Dict[str, Any]) -> str:
    totals = report.get("totals") or {}
    session = report.get("session") or {}
    lines = [
        "# 串口监控分析",
        "",
        f"- 分析时间：{report.get('analyzed_at')}",
        f"- 目录：`{report.get('run_dir')}`",
        f"- OTA 判定：**{report.get('ota_status')}**",
        f"- 会话：state={session.get('state')} reason={session.get('detach_reason')} "
        f"attach={session.get('attach_at')} detach={session.get('detach_at')} "
        f"resume={session.get('resume_count')} gaps={len(session.get('gaps') or [])}",
        "",
        "## 汇总",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 端口数 | {totals.get('ports')} |",
        f"| 有数据端口 | {totals.get('ports_with_data')} |",
        f"| 总字节 | {totals.get('bytes')} |",
        f"| 总行数 | {totals.get('lines')} |",
        f"| OTA 成功标记 | {totals.get('ota_success_markers')} |",
        f"| OTA 失败标记 | {totals.get('ota_failure_markers')} |",
        f"| OTA 提及 | {totals.get('ota_mentions')} |",
        f"| 联网标记 | {totals.get('network_markers')} |",
        f"| 重启标记 | {totals.get('reboot_markers')} |",
        f"| 超过 {GAP_THRESHOLD_S}s 的缺口 | {totals.get('gaps_over_threshold')} |",
        "",
        "## 分端口",
        "",
        "| 文件 | 字节 | 行数 | 首行 | 末行 | 最大间隔(s) | OTA 成功/失败/提及 |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, item in (report.get("ports") or {}).items():
        lines.append(
            f"| {name} | {item['bytes']} | {item['lines']} | {item['first_line_at']} | "
            f"{item['last_line_at']} | {item['max_gap_s']} | "
            f"{item['ota_success_markers']}/{item['ota_failure_markers']}/{item['ota_mentions']} |"
        )
    failures = [
        (name, sample)
        for name, item in (report.get("ports") or {}).items()
        for sample in item.get("failure_samples") or []
    ]
    if failures:
        lines.extend(["", "## OTA 失败样例行", ""])
        for name, sample in failures[:20]:
            lines.append(f"- `{name}` {sample}")
    lines.extend([
        "",
        "## 判定规则",
        "",
        f"- 只有出现成功标记且无失败标记才判 `OBSERVED_SUCCESS`；仅有关键词出现判 `OBSERVED_NO_VERDICT`。",
        f"- 无任何 OTA 关键词判 `NOT_OBSERVED`，不推断 OTA 结果。",
        f"- 相邻日志间隔超过 {GAP_THRESHOLD_S}s 记一次缺口，用于识别采集中断。",
        "",
    ])
    return "\n".join(lines)


def dump(report: Dict[str, Any], out_dir: Path) -> Dict[str, str]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "analysis.json"
    md_path = out_dir / "analysis.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report) + "\n", encoding="utf-8")
    return {"analysis_json": str(json_path), "analysis_md": str(md_path)}
