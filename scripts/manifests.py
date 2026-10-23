"""Optional project manifest loaders for contract references.

The manifests are deliberately project-owned and are never written into a
result directory.  They only provide IDs used by case contracts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping


class ManifestError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        super().__init__(f"{self.code}: {message}")


def _read_payload(path: Path | str) -> Any:
    file_path = Path(path)
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ManifestError("MANIFEST_READ_FAILED", f"{file_path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ManifestError("MANIFEST_JSON_INVALID", f"{file_path}: {error}") from error


def _extract_ids(payload: Any, *, key: str, id_fields: Iterable[str], label: str) -> frozenset[str]:
    if isinstance(payload, Mapping):
        version = payload.get("schema_version", 1)
        if version != 1:
            raise ManifestError("MANIFEST_SCHEMA_UNSUPPORTED", f"{label}: unsupported schema_version {version!r}")
        items = payload.get(key)
    else:
        items = payload
    if not isinstance(items, list):
        raise ManifestError("MANIFEST_INVALID", f"{label}: {key} must be a list")
    seen: set[str] = set()
    fields = tuple(id_fields)
    for index, item in enumerate(items):
        if isinstance(item, str):
            value = item
        elif isinstance(item, Mapping):
            value = next((item[field] for field in fields if item.get(field) not in (None, "")), "")
        else:
            value = ""
        text = str(value or "").strip()
        if not text:
            raise ManifestError("MANIFEST_INVALID", f"{label}[{index}] requires an id")
        if text in seen:
            raise ManifestError("MANIFEST_DUPLICATE_ID", f"{label}: duplicate id {text}")
        seen.add(text)
    return frozenset(seen)


def load_requirements(path: Path | str) -> frozenset[str]:
    return _extract_ids(
        _read_payload(path),
        key="requirements",
        id_fields=("id", "requirement_id"),
        label=str(path),
    )


def load_corpus(path: Path | str) -> frozenset[str]:
    payload = _read_payload(path)
    if isinstance(payload, Mapping) and "corpora" in payload:
        return _extract_ids(payload, key="corpora", id_fields=("id", "corpus_id"), label=str(path))
    return _extract_ids(payload, key="corpus", id_fields=("id", "corpus_id"), label=str(path))
