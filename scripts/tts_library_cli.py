#!/usr/bin/env python3
"""1006 共享 TTS 音频库的轻量消费 CLI（`ark.tts-spec.v1`）。

为什么是独立入口：``scripts/lstest.py`` 在模块导入阶段就硬依赖 ``lstts_core``，
把新子命令挂进去会让「查库」这个**合成之前**的步骤在没装合成依赖时无法运行。
本入口只 import ``tts_library``（纯标准库），因此 spec/lookup/fetch 可独立使用；
lstest 的既有子命令与参数完全不变。

退出码：0 成功；1 通用库错误；2 用法错误；3 库不可达；4 拒绝/越权。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

try:  # 作为包内模块导入
    from .tts_library import (
        DEFAULT_ENGINE,
        DEFAULT_LANGUAGE,
        DEFAULT_NORMALIZATION_PROFILE,
        LibraryError,
        LibraryForbiddenError,
        LibraryUnreachableError,
        TtsLibraryAdapter,
        UsageError,
        build_spec,
        requirement_to_spec,
    )
except ImportError:  # 直接执行脚本
    from tts_library import (  # type: ignore[no-redef]
        DEFAULT_ENGINE,
        DEFAULT_LANGUAGE,
        DEFAULT_NORMALIZATION_PROFILE,
        LibraryError,
        LibraryForbiddenError,
        LibraryUnreachableError,
        TtsLibraryAdapter,
        UsageError,
        build_spec,
        requirement_to_spec,
    )

EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_UNREACHABLE = 3
EXIT_FORBIDDEN = 4


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="1006 TTS 音频库轻量消费：只构造规格并调用 asset-tools，不实现缓存"
    )
    sub = parser.add_subparsers(dest="action", required=True)

    spec = sub.add_parser("spec", help="构造 ark.tts-spec.v1 并输出 JSON")
    source = spec.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--requirement", type=Path, help="JSON 需求映射（测试用例可直接复用）")
    spec.add_argument("--voice")
    spec.add_argument("--voice-ref-sha256")
    spec.add_argument("--language", default=DEFAULT_LANGUAGE)
    spec.add_argument("--engine", default=DEFAULT_ENGINE)
    spec.add_argument("--params", help="synthesis_params JSON（rate/pitch/style/emotion/speed）")
    spec.add_argument("--output", help="output JSON（format/sample_rate/channels）")
    spec.add_argument("--normalization-profile", default=DEFAULT_NORMALIZATION_PROFILE)

    for action, helptext in (("lookup", "查库（只看索引，不取件）"), ("fetch", "取件到任务目录")):
        command = sub.add_parser(action, help=helptext)
        command.add_argument("--spec", type=Path, required=True, help="ark.tts-spec.v1 JSON")
        command.add_argument("--task-dir", type=Path, default=Path("."), help="任务目录（默认当前目录）")
        command.add_argument("--asset-tools-bin", default="asset-tools", help="asset-tools 可执行文件")
        if action == "fetch":
            command.add_argument(
                "--dest", default="assets/tts/original", help="相对任务目录的取件目录（复制）"
            )
    return parser


def run(args: argparse.Namespace) -> int:
    if args.action == "spec":
        if args.requirement is not None:
            requirement = _load_json(args.requirement)
            if not isinstance(requirement, dict):
                raise UsageError("requirement JSON must be an object")
            spec, warnings = requirement_to_spec(requirement)
            for warning in warnings:
                print(warning, file=sys.stderr)
        else:
            params = json.loads(args.params) if args.params else None
            output = json.loads(args.output) if args.output else None
            spec = build_spec(
                args.text,
                voice=args.voice,
                language=args.language,
                engine=args.engine,
                synthesis_params=params,
                output=output,
                normalization_profile=args.normalization_profile,
                voice_ref_sha256=args.voice_ref_sha256,
            )
        print(json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    spec = _load_json(args.spec)
    if not isinstance(spec, dict):
        raise UsageError("spec JSON must be an object")
    adapter = TtsLibraryAdapter(args.asset_tools_bin, args.task_dir)
    if args.action == "lookup":
        payload = adapter.lookup(spec)
    else:
        payload = adapter.fetch(spec, dest=args.dest)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except UsageError as error:
        code, label, message = EXIT_USAGE, "USAGE", str(error)
    except LibraryUnreachableError as error:
        code, label, message = EXIT_UNREACHABLE, "UNREACHABLE", str(error)
    except LibraryForbiddenError as error:
        code, label, message = EXIT_FORBIDDEN, "FORBIDDEN", str(error)
    except LibraryError as error:
        code, label, message = EXIT_ERROR, "FAIL", str(error)
    except (ValueError, OSError) as error:
        code, label, message = EXIT_USAGE, "USAGE", str(error)
    print(
        json.dumps({"status": label, "reason": message}, ensure_ascii=False),
        file=sys.stderr,
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
