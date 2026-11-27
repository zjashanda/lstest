"""把项目自定义 code 整理成知识提案，供审核后并入码表。

用法：

    python -m lstest.scripts.marker_candidate --profile <profile.json> --catalog <catalog.json>
    python -m lstest.scripts.marker_candidate --profile ... --catalog ... --push \
        --api http://127.0.0.1:8091 --issue LING-118 --run r-1 --agent jinyiwei --node win003

默认只输出草案 JSON，不联网。``--push`` 时按匿名提案契约提交到知识服务，
落地为 pending，人工审核后才进 active。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Sequence

from .markers import load_catalog, load_custom_defs, load_rules


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _slug(text: str, limit: int = 32) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in text)
    return cleaned.strip("-")[:limit] or "marker"


def build_drafts(profile_path: Path, catalog_path: Path) -> list[dict[str, Any]]:
    profile_path = Path(profile_path).resolve()
    catalog = load_catalog(catalog_path)
    rules = load_rules(profile_path, catalog)
    custom_defs = load_custom_defs(profile_path)
    profile_sha = _sha256(profile_path)
    profile_bytes = profile_path.stat().st_size

    drafts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rule in rules:
        if not rule.custom or rule.code in seen:
            continue
        seen.add(rule.code)
        definition = custom_defs.get(rule.code, {})
        desc = str(definition.get("desc") or "").strip()
        semantics = str(definition.get("value_semantics") or "").strip()
        sample = str(definition.get("sample") or "").strip()
        if not (desc and semantics and sample):
            raise ValueError(f"自定义 code {rule.code} 定义不完整，拒绝上报")
        claim = f"{desc}；值域：{semantics}；样例：{sample}；正则匹配 rule_id={rule.rule_id}"
        drafts.append({
            "schema": "entry.v1",
            "id": "kb-knowledge-" + hashlib.sha256(f"{rule.code}:{profile_sha}".encode()).hexdigest()[:12],
            "type": "knowledge",
            "scope": {
                "device": None,
                "project": _slug(profile_path.parent.name, 48),
                "firmware": None,
                "platform": "lstest-profile",
            },
            "title": f"自定义标记 {rule.code}：{desc}",
            "claim": claim,
            "evidence": [{
                "kind": "file",
                "ref": profile_path.name,
                "bytes": profile_bytes,
                "sha256": profile_sha,
                "note": f"rule_id={rule.rule_id}；sample={sample}",
            }],
            "confidence": "medium",
            "source_skill": None,
            "tags": ["marker", "custom-code", rule.code],
        })
    return drafts


def push(drafts: Sequence[dict[str, Any]], api: str, provenance: dict[str, str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for draft in drafts:
        key = "marker-" + hashlib.sha256(
            f"{draft['id']}:{provenance.get('run', '')}".encode()
        ).hexdigest()[:24]
        body = json.dumps({
            "draft": draft,
            "author": provenance.get("agent"),
            "idempotency_key": key,
            "provenance": provenance,
        }, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            api.rstrip("/") + "/v1/proposals",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
                results.append({"code": draft["title"], "status": response.status, "response": payload})
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:300]
            results.append({"code": draft["title"], "status": error.code, "error": detail})
        except urllib.error.URLError as error:
            results.append({"code": draft["title"], "status": 0, "error": str(error)})
    return results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="项目自定义 code → 知识提案")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--out", help="草案输出路径；默认打印到 stdout")
    parser.add_argument("--push", action="store_true", help="提交到知识服务（匿名 pending）")
    parser.add_argument("--api", default="http://127.0.0.1:8091")
    parser.add_argument("--issue", default="")
    parser.add_argument("--run", default="")
    parser.add_argument("--agent", default="")
    parser.add_argument("--node", default="")
    args = parser.parse_args(argv)

    try:
        drafts = build_drafts(Path(args.profile), Path(args.catalog))
    except ValueError as error:
        print(f"BLOCKED_PROFILE_CONTRACT {error}", file=sys.stderr)
        return 2

    if not drafts:
        print("没有自定义 code，无需上报", file=sys.stderr)
        return 0

    if args.out:
        Path(args.out).write_text(json.dumps(drafts, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"草案已写入 {args.out}（{len(drafts)} 条）", file=sys.stderr)
    else:
        print(json.dumps(drafts, ensure_ascii=False, indent=2))

    if not args.push:
        return 0

    provenance = {"issue": args.issue, "run": args.run, "agent": args.agent, "node": args.node}
    missing = [name for name, value in provenance.items() if not value.strip()]
    if missing:
        print(f"--push 需要 {', '.join(missing)}", file=sys.stderr)
        return 2
    results = push(drafts, args.api, provenance)
    for item in results:
        state = "ok" if item.get("status") in (200, 201, 202) else "fail"
        print(f"  [{state}] {item['code']} -> {item.get('status')} {item.get('error', '')}".rstrip())
    return 0 if all(item.get("status") in (200, 201, 202) for item in results) else 1


if __name__ == "__main__":
    sys.exit(main())
