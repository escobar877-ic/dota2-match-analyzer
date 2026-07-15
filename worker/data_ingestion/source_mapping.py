from __future__ import annotations

import json
import re
import sys
from difflib import SequenceMatcher
from difflib import get_close_matches
from pathlib import Path
from typing import Any

backend_dir = Path(__file__).resolve().parents[2] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.tier_filter.tier1_config_loader import load_tier1_config
from app.tier_filter.tier1_matcher import Tier1Matcher


DEFAULT_SOURCE_MAPPING_PATH = Path("config/source_mappings.json")


def load_source_mappings(path: str | Path = DEFAULT_SOURCE_MAPPING_PATH) -> dict[str, dict[str, dict[str, str]]]:
    mapping_path = Path(path)
    if not mapping_path.exists():
        return {}
    data = json.loads(mapping_path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def resolve_source_team(
    source: str,
    external_id: str | None,
    raw_name: str | None,
    mappings: dict[str, Any] | None = None,
) -> str | None:
    return _resolve_mapping(source, "teams", external_id, raw_name, mappings)


def resolve_source_tournament(
    source: str,
    external_id: str | None,
    raw_name: str | None,
    mappings: dict[str, Any] | None = None,
) -> str | None:
    return _resolve_mapping(source, "tournaments", external_id, raw_name, mappings)


TEAM_VARIANT_TOKENS = {"academy", "junior", "juniors", "youth", " ii", " 2", " u21", "u21"}


def suggest_alias_matches(raw_name: str | None, known_aliases: list[str] | set[str], limit: int = 3) -> list[dict[str, str]]:
    if not raw_name:
        return []
    by_normalized = {_normalize(value): value for value in known_aliases if value}
    matches = get_close_matches(_normalize(raw_name), list(by_normalized), n=limit, cutoff=0.60)
    suggestions = []
    for match in matches:
        canonical = by_normalized[match]
        risk, reason = classify_alias_suggestion(raw_name, canonical)
        suggestions.append(
            {
                "raw_name": str(raw_name),
                "suggested_canonical": canonical,
                "risk": risk,
                "reason": reason,
            }
        )
    return suggestions


def classify_alias_suggestion(raw_name: str | None, canonical_name: str | None) -> tuple[str, str]:
    raw_norm = _normalize(raw_name)
    canonical_norm = _normalize(canonical_name)
    raw_tokens = set(raw_norm.split())
    canonical_tokens = set(canonical_norm.split())
    if not raw_norm or not canonical_norm:
        return "blocked", "Missing raw or canonical name."
    if raw_norm == canonical_norm:
        return "safe", "Exact normalized name match."
    if _has_team_variant_token(raw_norm) and not _has_team_variant_token(canonical_norm):
        return "blocked", "Academy/junior/youth team cannot map to main roster automatically."
    overlap = len(raw_tokens & canonical_tokens)
    union = len(raw_tokens | canonical_tokens) or 1
    token_similarity = overlap / union
    fuzzy_similarity = SequenceMatcher(None, raw_norm, canonical_norm).ratio()
    if token_similarity < 0.5:
        return "blocked", "Normalized tokens differ too much for automatic mapping."
    if fuzzy_similarity < 0.84:
        return "blocked", "Fuzzy similarity is too weak for automatic mapping."
    if token_similarity < 0.8:
        return "risky", "Names are similar but require manual verification."
    return "safe", "Strong token and fuzzy similarity."


def validate_source_mapping(path: str | Path = DEFAULT_SOURCE_MAPPING_PATH) -> dict[str, Any]:
    mappings = load_source_mappings(path)
    matcher = Tier1Matcher()
    invalid: list[dict[str, str]] = []
    mapped_teams_count = 0
    mapped_tournaments_count = 0

    for source, source_mapping in mappings.items():
        teams = source_mapping.get("teams", {}) if isinstance(source_mapping, dict) else {}
        tournaments = source_mapping.get("tournaments", {}) if isinstance(source_mapping, dict) else {}
        for key, value in teams.items():
            canonical = _mapping_canonical(value)
            mapped_teams_count += 1
            if not matcher.is_tier1_team(str(canonical)):
                invalid.append(
                    {"source": str(source), "kind": "team", "key": str(key), "canonical_name": str(canonical)}
                )
                continue
            invalid_reason = _validate_team_mapping_safety(str(key), value, str(canonical))
            if invalid_reason:
                invalid.append(
                    {
                        "source": str(source),
                        "kind": "team",
                        "key": str(key),
                        "canonical_name": str(canonical),
                        "reason": invalid_reason,
                    }
                )
        for key, value in tournaments.items():
            canonical = _mapping_canonical(value)
            mapped_tournaments_count += 1
            if not matcher.is_tier1_tournament(str(canonical)):
                invalid.append(
                    {"source": str(source), "kind": "tournament", "key": str(key), "canonical_name": str(canonical)}
                )
                continue
            if _looks_like_qualifier(str(key)) and not matcher.is_tier1_tournament(str(key)):
                invalid.append(
                    {
                        "source": str(source),
                        "kind": "tournament",
                        "key": str(key),
                        "canonical_name": str(canonical),
                        "reason": "Qualifier tournament cannot map to Tier 1 canonical unless explicitly allowlisted.",
                    }
                )

    return {
        "status": "failed" if invalid else "ok",
        "mapped_teams_count": mapped_teams_count,
        "mapped_tournaments_count": mapped_tournaments_count,
        "invalid_mappings_count": len(invalid),
        "invalid_mappings": invalid,
        "mapping_path": str(path),
    }


def known_team_aliases() -> list[str]:
    config = load_tier1_config()
    names: list[str] = []
    for team in config.teams:
        if not team.active:
            continue
        names.append(team.name)
        names.extend(team.aliases)
    return sorted(set(names))


def known_tournament_aliases() -> list[str]:
    config = load_tier1_config()
    names: list[str] = []
    for tournament in config.tournaments:
        if not tournament.active:
            continue
        names.append(tournament.name)
        names.extend(tournament.aliases)
    return sorted(set(names))


def _resolve_mapping(
    source: str,
    kind: str,
    external_id: str | None,
    raw_name: str | None,
    mappings: dict[str, Any] | None,
) -> str | None:
    active_mappings = mappings if mappings is not None else load_source_mappings()
    source_mapping = active_mappings.get(source, {})
    values = source_mapping.get(kind, {}) if isinstance(source_mapping, dict) else {}
    if not isinstance(values, dict):
        return None
    if external_id is not None and str(external_id) in values:
        return str(_mapping_canonical(values[str(external_id)]))
    normalized_name = _normalize(raw_name)
    for key, canonical in values.items():
        if _normalize(str(key)) == normalized_name:
            return str(_mapping_canonical(canonical))
    return None


def _mapping_canonical(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("canonical_name") or value.get("canonical") or value.get("name") or "")
    return str(value)


def _validate_team_mapping_safety(raw_key: str, value: Any, canonical: str) -> str | None:
    if _looks_like_external_id(raw_key):
        return None
    metadata = value if isinstance(value, dict) else {}
    allow_academy = bool(metadata.get("allow_academy_to_main", False))
    manual_verified = bool(metadata.get("manual_verified", False))
    verification_note = str(metadata.get("verification_note") or "").strip()
    risk, reason = classify_alias_suggestion(raw_key, canonical)
    if risk == "blocked" and _has_team_variant_token(_normalize(raw_key)) and not allow_academy:
        return "Academy/junior/youth mapping to main team requires allow_academy_to_main=true."
    if risk == "blocked" and (not manual_verified or not verification_note):
        return f"Raw name is too different from canonical; manual_verified=true and verification_note are required. {reason}"
    return None


def _has_team_variant_token(value: str) -> bool:
    padded = f" {value} "
    return any(token in padded for token in TEAM_VARIANT_TOKENS)


def _looks_like_qualifier(value: str) -> bool:
    normalized = _normalize(value)
    return "qualifier" in normalized or "qualification" in normalized


def _looks_like_external_id(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9A-Za-z:_-]+", value)) and not any(char.isspace() for char in value)


def _normalize(value: str | None) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("_", " ").replace("-", " ")).strip().casefold()
