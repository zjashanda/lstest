#!/usr/bin/env python3
"""Command-line entry point for the reusable lstest foundation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PACKAGE_ROOT.parent
SCRIPT_ROOT = Path(__file__).resolve().parent
for import_root in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

try:
    from .core import ConnectionSpec
    from .profile import DeviceProfile, ProfileError
    from .smoke import run_smoke
except ImportError:
    from core import ConnectionSpec
    from profile import DeviceProfile, ProfileError
    from smoke import run_smoke


def parse_ports(values: list[str], baudrate: int) -> list[dict[str, object]]:
    result = []
    for value in values:
        parts = value.split(":")
        port = parts[0].strip()
        role = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
        if not port:
            raise ValueError("port cannot be empty")
        result.append({"port": port, "role": role, "baudrate": baudrate})
    return result


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="lstest 通用设备初始化与基础测试运行时")
    sub = value.add_subparsers(dest="command", required=True)
    list_cmd = sub.add_parser("list", help="列出公共能力")
    list_cmd.add_argument("--json", action="store_true")
    pre = sub.add_parser("preflight", help="只检查 profile 和输入，不打开硬件")
    pre.add_argument("--profile", type=Path, required=True)
    pre.add_argument("--port", action="append", default=[])
    pre.add_argument("--baudrate", type=int, default=0)
    pre.add_argument("--playback-device-key")
    pre.add_argument("--result-root", type=Path, default=Path("result"))
    for name in ("init", "smoke"):
        command = sub.add_parser(name, help="执行基础初始化 smoke")
        command.add_argument("--profile", type=Path, required=True)
        command.add_argument("--port", action="append", default=[])
        command.add_argument("--baudrate", type=int, default=0)
        command.add_argument("--playback-device-key")
        command.add_argument("--capture-device-key")
        command.add_argument("--result-root", type=Path, default=Path("result"))
        command.add_argument("--hardware", action="store_true", help="允许打开串口和播放设备")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "list":
        payload = {"skill": "lstest", "capabilities": ["dynamic-serial", "profile", "evidence", "basic-smoke", "safe-stop"]}
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else "\n".join(payload["capabilities"]))
        return 0
    try:
        if args.baudrate <= 0 and args.port:
            raise ValueError("--baudrate must be positive when ports are supplied")
        connection = ConnectionSpec.from_mapping({"ports": parse_ports(args.port, args.baudrate) if args.port else [], "playback_device_key": args.playback_device_key, "capture_device_key": getattr(args, "capture_device_key", None), "profile_id": str(args.profile), "result_root": str(args.result_root)})
        profile = DeviceProfile.load(args.profile)
        if args.command == "preflight":
            print(json.dumps({"status": "PASS", "profile_id": profile.profile_id, "profile_sha256": profile.sha256, "connection": connection.to_dict()}, ensure_ascii=False, indent=2))
            return 0
        summary = run_smoke(connection, args.profile, hardware=args.hardware)
        return 0 if summary["status"] in {"PASS", "WARN"} else 2
    except (ValueError, ProfileError) as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
