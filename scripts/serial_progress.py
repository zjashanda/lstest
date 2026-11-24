"""Incremental progress scanner for a running serial monitor window.

Reads each ``serial_<port>_<role>.log`` from the last byte offset and reports
what changed, so a 5-minute cadence can summarise OTA progress without
rescanning the whole file and without trusting Windows file-size metadata
(which stays stale while another process holds the handle).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

LINE_RE = re.compile(r"^\[(?P<ts>[^\]]+)\] (?P<port>\S+) (?P<role>\S+) #(?P<n>\d+) (?P<text>.*)$")

STAGES = (
    ("download_progress", re.compile(r"ota_dl_csk|ota write|downloading", re.I)),
    ("download_complete", re.compile(r"download completa|download complete", re.I)),
    ("check_pass", re.compile(r"ota check pass", re.I)),
    ("upgrade_end", re.compile(r"APP upgrade end", re.I)),
    ("reboot_request", re.compile(r"want reboot|will reboot|ota reboot timer", re.I)),
    ("boot", re.compile(r"bootloader application|boot reason", re.I)),
    ("abnormal_boot", re.compile(r"unnomally reboot|not normal boot", re.I)),
    ("ota_fail", re.compile(r"ota fail|upgrade fail|verify fail|download fail|ota error", re.I)),
)

VERSION_PATTERNS = (
    re.compile(r"venus ap appliction\((?P<v>[^)]+)\)", re.I),
    re.compile(r"APP Version:\s*(?P<v>[0-9A-Za-z._]+)", re.I),
    re.compile(r"\b(?P<v>V\d+\.\d+\.\d+@[A-Za-z]{3}\s+\d+\s+\d{4}[^,;]*)", re.I),
)

OFFSET_RE = re.compile(r"offset\s+([0-9]{2,})", re.I)


class ProgressScanner:
    def __init__(self, serial_dir: Path) -> None:
        self.serial_dir = Path(serial_dir)
        self.offsets: Dict[str, int] = {}
        self.totals: Dict[str, Dict[str, int]] = {}

    def files(self) -> List[Path]:
        return sorted(self.serial_dir.glob("*.log")) if self.serial_dir.is_dir() else []

    def scan(self) -> Dict[str, Any]:
        ports: Dict[str, Dict[str, Any]] = {}
        new_lines_total = 0
        versions: Dict[str, int] = {}
        stage_totals: Dict[str, int] = {}

        for path in self.files():
            name = path.name
            start = self.offsets.get(name, 0)
            try:
                with path.open("rb") as handle:
                    handle.seek(start)
                    data = handle.read()
                    end = handle.tell()
            except OSError:
                continue
            self.offsets[name] = end

            text = data.decode("utf-8", errors="replace")
            lines = 0
            stages: Dict[str, int] = {}
            max_offset = 0
            last_line = ""
            for raw in text.splitlines():
                match = LINE_RE.match(raw)
                if not match:
                    continue
                lines += 1
                body = match.group("text")
                last_line = body[:200]
                for name_key, pattern in STAGES:
                    if pattern.search(body):
                        stages[name_key] = stages.get(name_key, 0) + 1
                        stage_totals[name_key] = stage_totals.get(name_key, 0) + 1
                found = OFFSET_RE.search(body)
                if found:
                    max_offset = max(max_offset, int(found.group(1)))
                for pattern in VERSION_PATTERNS:
                    hit = pattern.search(body)
                    if hit:
                        key = hit.group("v").strip()
                        versions[key] = versions.get(key, 0) + 1

            new_bytes = end - start
            new_lines_total += lines
            record = self.totals.setdefault(name, {"bytes": 0, "lines": 0})
            record["bytes"] += new_bytes
            record["lines"] += lines
            ports[name] = {
                "new_bytes": new_bytes,
                "new_lines": lines,
                "offset": end,
                "stages": stages,
                "max_ota_offset": max_offset,
                "last_line": last_line,
            }

        return {
            "ports": ports,
            "new_lines_total": new_lines_total,
            "stages": stage_totals,
            "versions": versions,
            "totals": self.totals,
        }


def verdict(payload: Dict[str, Any]) -> str:
    stages = payload.get("stages") or {}
    versions = payload.get("versions") or {}
    if stages.get("ota_fail"):
        return "本轮出现 OTA 失败标记，需立即取证"
    if stages.get("download_complete") and len(versions) >= 2:
        return "本轮完成下载且出现两个以上版本串，需判断是升级生效还是回滚"
    if stages.get("download_complete"):
        return "本轮完成下载并请求重启，等待版本回读"
    if stages.get("download_progress"):
        return "本轮仍在下载"
    if payload.get("new_lines_total", 0) == 0:
        return "本轮无新增日志"
    return "本轮无 OTA 动作"
