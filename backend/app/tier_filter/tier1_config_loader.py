from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.tier_filter.schemas import Tier1Config, Tier1TeamConfig, Tier1TournamentConfig


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TEAMS_PATH = None
DEFAULT_TOURNAMENTS_PATH = None


class Tier1ConfigError(ValueError):
    pass


@lru_cache(maxsize=1)
def load_tier1_config(
    teams_path: str | Path | None = DEFAULT_TEAMS_PATH,
    tournaments_path: str | Path | None = DEFAULT_TOURNAMENTS_PATH,
) -> Tier1Config:
    teams_payload = _load_json_list(_resolve_config_path("tier1_teams.json", teams_path))
    tournaments_payload = _load_json_list(_resolve_config_path("tier1_tournaments.json", tournaments_path))

    try:
        teams = [Tier1TeamConfig.model_validate(item) for item in teams_payload]
        tournaments = [Tier1TournamentConfig.model_validate(item) for item in tournaments_payload]
    except ValidationError as exc:
        raise Tier1ConfigError(f"Invalid Tier 1 config structure: {exc}") from exc

    return Tier1Config(
        teams=[team for team in teams if team.active],
        tournaments=[tournament for tournament in tournaments if tournament.active],
    )


def _resolve_config_path(filename: str, explicit_path: str | Path | None = None) -> Path:
    if explicit_path is not None:
        return Path(explicit_path)

    config_dir = os.getenv("TIER1_CONFIG_DIR")
    candidates = []
    if config_dir:
        candidates.append(Path(config_dir) / filename)
    candidates.extend(
        [
            PROJECT_ROOT / "config" / filename,
            Path.cwd() / "config" / filename,
            Path("/app/config") / filename,
        ]
    )

    for path in candidates:
        if path.exists():
            return path

    return candidates[0]


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise Tier1ConfigError(f"Tier 1 config file not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    if not isinstance(payload, list):
        raise Tier1ConfigError(f"Tier 1 config must be a list: {path}")
    if not all(isinstance(item, dict) for item in payload):
        raise Tier1ConfigError(f"Tier 1 config entries must be objects: {path}")

    return payload
