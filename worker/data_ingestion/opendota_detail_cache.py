from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ml.config import ML_ARTIFACT_DIR


DEFAULT_CACHE_DIR = Path(ML_ARTIFACT_DIR) / "source_cache" / "opendota_matches"


def load_cached_match_detail(
    match_id: str,
    *,
    cache_dir: str | Path | None = None,
) -> dict[str, Any] | None:
    if not str(match_id).strip().isdigit():
        return None
    path = _cache_path(match_id, cache_dir)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or str(raw.get("match_id") or "") != str(match_id):
        return None
    return raw


def write_cached_match_detail(
    match_id: str,
    raw: dict[str, Any],
    *,
    cache_dir: str | Path | None = None,
) -> Path:
    normalized_id = str(match_id).strip()
    if not normalized_id.isdigit():
        raise ValueError("OpenDota match id must contain only digits.")
    if str(raw.get("match_id") or "") != normalized_id:
        raise ValueError("OpenDota detail payload does not match the requested match id.")
    path = _cache_path(normalized_id, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
    return path


def _cache_path(match_id: str, cache_dir: str | Path | None) -> Path:
    root = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
    return root / f"{str(match_id).strip()}.json"
