"""把验证过的 lstest 修改上传到 8090 证据存储，供 bszT14 审核员复核。

执行节点只上传，不创建候选、不碰 8091。

用法：
    python -m lstest.scripts.upload_lstest_patch \
        --task NOAH-413 --run r-1 --base-commit 5820427 --base-version 2.8.2 \
        --file profiles/demo/profile.json --file markers/catalog.json \
        --checks '{"unclaimed_lines": 12, "silent_rules": 0, "preflight_pass": true}' \
        --log-sample work/patch-sample.log

默认写到 http://10.11.63.24:8090（8090 的宿主机入口）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Sequence

DEFAULT_API = os.environ.get("ARK_STORAGE_API", "http://10.11.63.24:8090")
CATEGORY = "evidence"
SCOPE = "noahsark"
MAX_LOG_SAMPLE = 50


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _put(api: str, node: str, key: str, data: bytes, sha: str, timeout: int = 60) -> dict[str, Any]:
    url = f"{api.rstrip('/')}/storage/v1/objects/{CATEGORY}/{SCOPE}/{key}"
    request = urllib.request.Request(
        url, data=data, method="PUT",
        headers={
            "X-Ark-Node": node,
            "X-Expected-SHA256": sha,
            "Content-Type": "application/octet-stream",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {"status": response.status, "body": json.loads(response.read().decode("utf-8"))}
    except urllib.error.HTTPError as error:
        return {"status": error.code, "body": error.read().decode("utf-8", "replace")[:300]}
    except urllib.error.URLError as error:
        return {"status": 0, "body": str(error)}


def _safe_rel(path: str) -> str:
    raw = path.replace("\\", "/")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise ValueError(f"非法相对路径（绝对路径）：{path}")
    if ".." in raw.split("/"):
        raise ValueError(f"非法相对路径（含上跳）：{path}")
    rel = raw.strip("/")
    if not rel:
        raise ValueError(f"非法相对路径（空）：{path}")
    return rel


def _read_log_sample(path: Path | None, inline: str | None) -> list[str]:
    if path is not None:
        lines = [line.rstrip("\n") for line in path.read_text(encoding="utf-8", errors="replace").splitlines()]
    elif inline:
        lines = [line for line in inline.splitlines() if line.strip()]
    else:
        return []
    lines = [line for line in lines if line.strip()]
    if len(lines) > MAX_LOG_SAMPLE:
        raise ValueError(f"log_sample 超过 {MAX_LOG_SAMPLE} 行（当前 {len(lines)}）")
    return lines


def build_manifest(
    *, node: str, task: str, run: str, base_commit: str, base_version: str,
    files: Sequence[Path], checks: dict[str, Any], log_sample: list[str],
    log_sample_note: str, root: Path,
) -> tuple[dict[str, Any], list[tuple[str, bytes, str]]]:
    commit = base_commit.strip().lower()
    if not (7 <= len(commit) <= 40) or any(ch not in "0123456789abcdef" for ch in commit):
        raise ValueError("base_commit 必须是 7-40 位十六进制")
    if not files:
        raise ValueError("至少提供一个 --file")
    if not checks:
        raise ValueError("checks 不能为空：审核员需要适配校验结果")
    if not log_sample:
        raise ValueError("缺少 log_sample：审核员需要最小复现样本")

    payloads: list[tuple[str, bytes, str]] = []
    entries: list[dict[str, Any]] = []
    for path in files:
        data = path.read_bytes()
        try:
            rel = _safe_rel(str(path.relative_to(root)) if path.is_absolute() else str(path))
        except ValueError:
            rel = _safe_rel(path.name)
        digest = _sha256(data)
        payloads.append((rel, data, digest))
        entries.append({"path": rel, "bytes": len(data), "sha256": digest})

    manifest = {
        "schema": "lstest.patch-upload.v1",
        "node": node,
        "task": task,
        "run": run,
        "base_commit": commit,
        "base_version": base_version or None,
        "files": entries,
        "checks": checks,
        "log_sample": log_sample,
        "log_sample_note": log_sample_note or None,
    }
    return manifest, payloads


def upload(api: str, node: str, task: str, run: str, manifest: dict[str, Any],
           payloads: Sequence[tuple[str, bytes, str]]) -> list[dict[str, Any]]:
    prefix = f"{node}/{task}/{run}/lstest-patch"
    results: list[dict[str, Any]] = []
    for rel, data, digest in payloads:
        results.append({"key": f"{prefix}/{rel}", **_put(api, node, f"{prefix}/{rel}", data, digest)})
    body = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    digest = _sha256(body)
    results.append({"key": f"{prefix}/manifest.json", **_put(api, node, f"{prefix}/manifest.json", body, digest)})
    return results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="上传验证过的 lstest 修改到 8090")
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--node", default=os.environ.get("COMPUTERNAME") or socket.gethostname())
    parser.add_argument("--task", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--base-commit", required=True)
    parser.add_argument("--base-version", default="")
    parser.add_argument("--file", action="append", default=[], dest="files")
    parser.add_argument("--checks", default="{}", help="适配校验结果 JSON")
    parser.add_argument("--log-sample", default=None, help="样本文件路径")
    parser.add_argument("--log-sample-note", default="")
    parser.add_argument("--root", default=".", help="相对路径基准目录")
    parser.add_argument("--dry-run", action="store_true", help="只生成清单，不上传")
    parser.add_argument("--out", default=None, help="清单输出路径")
    args = parser.parse_args(argv)

    try:
        checks = json.loads(args.checks) if args.checks.strip() else {}
        if not isinstance(checks, dict):
            raise ValueError("checks 必须是 JSON 对象")
        log_sample = _read_log_sample(Path(args.log_sample) if args.log_sample else None, None)
        manifest, payloads = build_manifest(
            node=args.node, task=args.task, run=args.run,
            base_commit=args.base_commit, base_version=args.base_version,
            files=[Path(p) for p in args.files], checks=checks,
            log_sample=log_sample, log_sample_note=args.log_sample_note,
            root=Path(args.root).resolve(),
        )
    except (ValueError, OSError) as error:
        print(f"拒绝上传：{error}", file=sys.stderr)
        return 2

    if args.out:
        Path(args.out).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"清单已写入 {args.out}", file=sys.stderr)
    else:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))

    if args.dry_run:
        print("dry-run：未上传", file=sys.stderr)
        return 0

    results = upload(args.api, args.node, args.task, args.run, manifest, payloads)
    failed = [item for item in results if item.get("status") not in (200, 201)]
    for item in results:
        print(f"  [{'ok' if item.get('status') in (200, 201) else 'fail'}] {item['key']} -> {item.get('status')}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
