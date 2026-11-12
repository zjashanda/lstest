#!/usr/bin/env python3
"""Command-line entry point for the reusable lstest foundation."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PACKAGE_ROOT.parent
SCRIPT_ROOT = Path(__file__).resolve().parent
for import_root in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

try:
    from .audio_synthesis import (
        DEFAULT_VOICE, load_text_manifest, synthesize_indextts, synthesize_manifest,
    )
    from .core import ConnectionSpec
    from .device_snapshot import DeviceSnapshot, load_device_snapshot
    from .profile import DeviceProfile, ProfileError
    from .smoke import run_smoke
except ImportError:
    from audio_synthesis import (
        DEFAULT_VOICE, load_text_manifest, synthesize_indextts, synthesize_manifest,
    )
    from core import ConnectionSpec
    from device_snapshot import DeviceSnapshot, load_device_snapshot
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


def declared_role_labels(profile: DeviceProfile) -> dict[str, tuple[str, ...]]:
    """profile 里每个 role 已声明的端口名/接口标签，供 raw snapshot 精确匹配。"""
    required: dict[str, tuple[str, ...]] = {}
    for entry in profile.ports:
        role = str(entry.get("role") or "").strip()
        if not role:
            continue
        required[role] = tuple(
            label for label in (
                str(entry.get("port") or "").strip(),
                str(entry.get("interface") or "").strip(),
            ) if label
        )
    return required


def apply_device_snapshot(
    ports: list[dict[str, object]],
    profile: DeviceProfile,
    snapshot: DeviceSnapshot,
    default_baudrate: int,
) -> list[dict[str, object]]:
    """用文档解析出的端口覆盖 profile 里同 role 的端口；未声明的 role 不动。"""
    result = [dict(item) for item in ports]
    by_role = {item.get("role"): item for item in result if item.get("role")}
    for entry in profile.ports:
        role = str(entry.get("role") or "").strip()
        if not role:
            continue
        port = snapshot.port_for(role)
        existing = by_role.get(role)
        if existing is not None:
            existing["port"] = port
            continue
        baudrate = int(entry.get("baudrate") or 0) or default_baudrate
        if baudrate <= 0:
            raise ValueError(f"--baudrate must be positive for role '{role}': profile port declares no baudrate")
        item: dict[str, object] = {
            "port": port,
            "role": role,
            "baudrate": baudrate,
            "capabilities": [str(value) for value in entry.get("capabilities", ())],
            "required_for": [str(value) for value in entry.get("required_for", ())],
        }
        result.append(item)
        by_role[role] = item
    return result


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="lstest 通用设备初始化与基础测试运行时")
    sub = value.add_subparsers(dest="command", required=True)
    list_cmd = sub.add_parser("list", help="列出公共能力")
    list_cmd.add_argument("--json", action="store_true")

    tts = sub.add_parser("tts", help="使用 IndexTTS 合成并校验一条 WAV 音频")
    text_source = tts.add_mutually_exclusive_group(required=True)
    text_source.add_argument("--text")
    text_source.add_argument("--text-file", type=Path, help="包含单条 UTF-8 语料的文本文件")
    tts.add_argument("--output", type=Path, required=True)
    tts.add_argument("--case-id", default="")
    tts.add_argument("--voice", default=DEFAULT_VOICE, help="本地音色名或参考音频路径")
    tts.add_argument("--emotion", default=None, help="可选情感参考音频路径")
    tts.add_argument("--timeout-s", type=float, default=60.0)
    tts.add_argument("--retries", type=int, default=1)

    tts_manifest = sub.add_parser("tts-manifest", help="按文本 manifest 批量合成并写入音频 manifest")
    tts_manifest.add_argument("--input", type=Path, required=True, help="包含 text 列的 UTF-8 CSV 或 JSONL")
    tts_manifest.add_argument("--output-dir", type=Path, required=True)
    tts_manifest.add_argument("--manifest-output", type=Path, required=True)
    tts_manifest.add_argument("--voice", default=DEFAULT_VOICE, help="默认音色，行内 voice 列可覆盖")
    tts_manifest.add_argument("--emotion", default=None, help="可选情感参考音频路径")
    tts_manifest.add_argument("--timeout-s", type=float, default=60.0)
    tts_manifest.add_argument("--retries", type=int, default=1)

    audio = sub.add_parser("audio", help="管理 laid、枚举声卡、探测或播放音频")
    audio_sub = audio.add_subparsers(dest="audio_command", required=True)
    ensure_laid = audio_sub.add_parser("ensure-laid", help="安装或刷新 laid / audio-list 到用户 shell profile")
    ensure_laid.add_argument("--platform", choices=("auto", "windows", "linux"), default="auto")
    ensure_laid.add_argument("--force", action="store_true")
    scan = audio_sub.add_parser("scan", help="扫描活动 ListenAI 声卡及稳定设备 key")
    scan.add_argument("--platform", choices=("auto", "windows", "linux"), default="auto")
    scan.add_argument("--direction", choices=("All", "Render", "Capture"), default="All")
    scan.add_argument("--json", action="store_true")
    probe = audio_sub.add_parser("probe", help="探测默认播放设备或指定稳定设备 key")
    probe.add_argument("--platform", choices=("auto", "windows", "linux"), default="auto")
    probe.add_argument("--device-key")
    play = audio_sub.add_parser("play", help="在默认或指定稳定设备 key 上播放单个音频")
    play.add_argument("--platform", choices=("auto", "windows", "linux"), default="auto")
    play.add_argument("--audio-file", type=Path, required=True)
    play.add_argument("--device-key")
    play.add_argument("--repeat", type=int, default=1)
    play.add_argument("--gap", type=float, default=0.0)
    play.add_argument("--skip-probe", action="store_true")
    dual = audio_sub.add_parser("dual-play", help="在两个默认或指定稳定设备 key 上并行播放")
    dual.add_argument("--platform", choices=("auto", "windows", "linux"), default="auto")
    dual.add_argument("--left-file", type=Path, required=True)
    dual.add_argument("--right-file", type=Path, required=True)
    dual.add_argument("--left-device-key")
    dual.add_argument("--right-device-key")
    dual.add_argument("--repeat", type=int, default=1)
    dual.add_argument("--gap", type=float, default=0.0)
    dual.add_argument("--skip-probe", action="store_true")
    eventlog = sub.add_parser("eventlog", help="结构化事件日志 v2：导入旧日志、渲染人读产物、校验一致性")
    eventlog_sub = eventlog.add_subparsers(dest="eventlog_command", required=True)
    build = eventlog_sub.add_parser("build", help="把旧版 29 字段平铺日志迁移为 events.jsonl + run.log + summary")
    build.add_argument("--legacy", type=Path, required=True, help="旧版平铺块日志（只读，不修改）")
    build.add_argument("--out-dir", type=Path, required=True, help="新的 v2 结果目录")
    build.add_argument("--task-id", default=None)
    build.add_argument("--meta", type=Path, default=None, help="任务上下文 JSON（title/purpose/scope/inputs 等）")
    build.add_argument("--raw-min-bytes", type=int, default=0, help="raw 外置阈值；0 表示全部外置")
    render = eventlog_sub.add_parser("render", help="从已有 events.jsonl 重新生成 run.log / summary.md / summary.json")
    render.add_argument("--run-dir", type=Path, required=True)
    render.add_argument("--meta", type=Path, default=None)
    verify = eventlog_sub.add_parser("verify", help="校验 v2 目录：schema、序号、重算一致、判定冲突、路径泄漏")
    verify.add_argument("--run-dir", type=Path, required=True)
    verify.add_argument("--check-files", action="store_true", help="同时校验证据文件存在与 SHA-256")
    verify.add_argument("--json", action="store_true")
    schema_cmd = eventlog_sub.add_parser("schema", help="输出 v2 事件/摘要 schema 自描述")
    schema_cmd.add_argument("--output", type=Path, default=None)

    pre = sub.add_parser("preflight", help="只检查 profile 和输入，不打开硬件")
    pre.add_argument("--profile", type=Path, required=True)
    pre.add_argument("--port", action="append", default=[])
    pre.add_argument("--baudrate", type=int, default=0)
    pre.add_argument("--playback-device-key")
    pre.add_argument("--result-root", type=Path, default=Path("result"))
    pre.add_argument(
        "--device-snapshot", type=Path,
        help="device-io 产出的 snapshot/role-map JSON，指定后端口全部来自该文档",
    )
    for name in ("init", "smoke"):
        command = sub.add_parser(name, help="执行基础初始化 smoke")
        command.add_argument("--profile", type=Path, required=True)
        command.add_argument("--port", action="append", default=[])
        command.add_argument("--baudrate", type=int, default=0)
        command.add_argument("--playback-device-key")
        command.add_argument("--capture-device-key")
        command.add_argument("--result-root", type=Path, default=Path("result"))
        command.add_argument("--hardware", action="store_true", help="允许打开串口和播放设备")
        command.add_argument(
            "--device-snapshot", type=Path,
            help="device-io 产出的 snapshot/role-map JSON，指定后端口全部来自该文档",
        )
    return value


def load_meta(path: Path | None) -> dict[str, object]:
    if path is None:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("--meta 必须是 JSON 对象")
    return payload


def run_eventlog_command(args: argparse.Namespace) -> int:
    """结构化事件日志 v2 的命令实现；不打开设备、不访问网络。"""
    try:
        from . import eventlog
    except ImportError:  # pragma: no cover - 直接执行回退
        import eventlog  # type: ignore

    meta = load_meta(getattr(args, "meta", None))
    if args.eventlog_command == "build":
        summary = eventlog.import_legacy_log(
            args.legacy, args.out_dir, task_id=args.task_id,
            raw_min_bytes=args.raw_min_bytes, meta=meta,
        )
    elif args.eventlog_command == "render":
        records = eventlog.read_events(args.run_dir / "events.jsonl")
        summary = eventlog.write_bundle(records, args.run_dir, meta=meta)
    elif args.eventlog_command == "verify":
        findings = eventlog.verify_bundle(args.run_dir, check_files=args.check_files)
        errors = [item for item in findings if item["level"] == "ERROR"]
        if args.json:
            print(json.dumps({"status": "FAIL" if errors else "PASS", "findings": findings}, ensure_ascii=False, indent=2))
        else:
            if not findings:
                print("PASS：v2 产物一致，无 ERROR/WARN")
            for item in findings:
                print(f"{item['level']:<5} {item['code']}: {item['message']}")
            print(f"结论：{'FAIL' if errors else 'PASS'}（ERROR={len(errors)}，WARN={len(findings) - len(errors)}）")
        return 2 if errors else 0
    else:  # schema
        payload = eventlog.schema_document()
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.output:
            args.output.write_text(text + "\n", encoding="utf-8")
            print(str(args.output))
        else:
            print(text)
        return 0
    print(json.dumps({
        "status": "BUILT",
        "result_dir": str(args.out_dir if args.eventlog_command == "build" else args.run_dir),
        "task_id": summary.get("task_id"),
        "events": (summary.get("meta") or {}).get("events_count"),
        "test_outcome": summary.get("test_outcome"),
        "delivery_status": summary.get("delivery_status"),
        "final_status": summary.get("final_status"),
        "round_success_rate": (summary.get("rates") or {}).get("round_success_rate"),
        "first_attempt_success_rate": (summary.get("rates") or {}).get("first_attempt_success_rate"),
        "failed_gate_checks": summary.get("delivery_failed_checks") or [],
    }, ensure_ascii=False, indent=2))
    return 0


def run_audio_tool(args: argparse.Namespace) -> int:
    """Delegate device binding to the bundled, independently testable tool."""
    script = SCRIPT_ROOT / "listenai_play.py"
    if not script.is_file():
        raise RuntimeError(f"bundled audio tool is missing: {script}")
    command = [sys.executable, str(script), args.audio_command, "--platform", args.platform]
    if args.audio_command == "ensure-laid":
        if args.force:
            command.append("--force")
    elif args.audio_command == "scan":
        command.extend(["--direction", args.direction])
        if args.json:
            command.append("--json")
    elif args.audio_command == "probe":
        if args.device_key:
            command.extend(["--device-key", args.device_key])
    elif args.audio_command == "play":
        command.extend(["--audio-file", str(args.audio_file), "--repeat", str(args.repeat), "--gap", str(args.gap)])
        if args.device_key:
            command.extend(["--device-key", args.device_key])
        if args.skip_probe:
            command.append("--skip-probe")
    elif args.audio_command == "dual-play":
        command.extend([
            "--left-file", str(args.left_file), "--right-file", str(args.right_file),
            "--repeat", str(args.repeat), "--gap", str(args.gap),
        ])
        if args.left_device_key:
            command.extend(["--left-device-key", args.left_device_key])
        if args.right_device_key:
            command.extend(["--right-device-key", args.right_device_key])
        if args.skip_probe:
            command.append("--skip-probe")
    else:  # pragma: no cover - argparse limits this value
        raise ValueError(f"unsupported audio command: {args.audio_command}")
    completed = subprocess.run(command, check=False)
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "list":
        payload = {
            "skill": "lstest",
            "capabilities": [
                "dynamic-serial", "profile", "evidence", "basic-smoke", "safe-stop",
                "indextts-tts", "audio-device-scan", "laid-installer", "device-key-playback",
                "structured-event-log-v2",
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else "\n".join(payload["capabilities"]))
        return 0
    try:
        if args.command == "eventlog":
            return run_eventlog_command(args)
        if args.command == "tts":
            text = args.text
            if args.text_file is not None:
                text = args.text_file.read_text(encoding="utf-8").strip()
            payload = synthesize_indextts(
                text, args.output, case_id=args.case_id, voice=args.voice,
                emotion=args.emotion, timeout_s=args.timeout_s, retries=args.retries,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if args.command == "tts-manifest":
            payload = synthesize_manifest(
                load_text_manifest(args.input), args.output_dir, args.manifest_output,
                voice=args.voice, emotion=args.emotion,
                timeout_s=args.timeout_s, retries=args.retries,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0 if payload["status"] == "PASS" else 2
        if args.command == "audio":
            return run_audio_tool(args)
        if args.baudrate <= 0 and args.port:
            raise ValueError("--baudrate must be positive when ports are supplied")
        ports = parse_ports(args.port, args.baudrate) if args.port else []
        profile = DeviceProfile.load(args.profile)
        snapshot_block = None
        snapshot_path = getattr(args, "device_snapshot", None)
        if snapshot_path is not None:
            snapshot = load_device_snapshot(snapshot_path, declared_role_labels(profile))
            ports = apply_device_snapshot(ports, profile, snapshot, args.baudrate)
            snapshot_block = snapshot.to_dict()
        connection = ConnectionSpec.from_mapping({"ports": ports, "playback_device_key": args.playback_device_key, "capture_device_key": getattr(args, "capture_device_key", None), "profile_id": str(args.profile), "result_root": str(args.result_root)})
        if args.command == "preflight":
            payload = {"status": "PASS", "profile_id": profile.profile_id, "profile_sha256": profile.sha256, "connection": connection.to_dict()}
            if snapshot_block is not None:
                payload["device_snapshot"] = snapshot_block
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        summary = run_smoke(connection, args.profile, hardware=args.hardware)
        if snapshot_block is not None:
            print(json.dumps({"status": summary.get("status"), "device_snapshot": snapshot_block}, ensure_ascii=False, indent=2))
        return 0 if summary["status"] in {"PASS", "WARN"} else 2
    except (ValueError, ProfileError, RuntimeError, OSError) as error:
        print(json.dumps({"status": "BLOCKED", "reason": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
