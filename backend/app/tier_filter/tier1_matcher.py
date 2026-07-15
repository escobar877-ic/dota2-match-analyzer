from __future__ import annotations

from app.tier_filter.schemas import Tier1Config
from app.tier_filter.tier1_config_loader import load_tier1_config


class Tier1Matcher:
    def __init__(self, config: Tier1Config | None = None) -> None:
        self.config = config or load_tier1_config()
        self.team_names = self._build_team_name_set()
        self.tournament_names = self._build_tournament_name_set()

    def is_tier1_team(self, name: str) -> bool:
        return _normalize_name(name) in self.team_names

    def is_tier1_tournament(self, name: str) -> bool:
        return _normalize_name(name) in self.tournament_names

    def is_tier1_match(self, team_a_name: str, team_b_name: str, tournament_name: str) -> bool:
        return (
            self.is_tier1_team(team_a_name)
            and self.is_tier1_team(team_b_name)
            and self.is_tier1_tournament(tournament_name)
        )

    def _build_team_name_set(self) -> set[str]:
        names: set[str] = set()
        for team in self.config.teams:
            if not team.active:
                continue
            names.add(_normalize_name(team.name))
            names.update(_normalize_name(alias) for alias in team.aliases)
        return names

    def _build_tournament_name_set(self) -> set[str]:
        names: set[str] = set()
        for tournament in self.config.tournaments:
            if not tournament.active:
                continue
            names.add(_normalize_name(tournament.name))
            names.update(_normalize_name(alias) for alias in tournament.aliases)
        return names


def is_tier1_team(name: str) -> bool:
    return Tier1Matcher().is_tier1_team(name)


def is_tier1_tournament(name: str) -> bool:
    return Tier1Matcher().is_tier1_tournament(name)


def is_tier1_match(team_a_name: str, team_b_name: str, tournament_name: str) -> bool:
    return Tier1Matcher().is_tier1_match(team_a_name, team_b_name, tournament_name)


def _normalize_name(value: str) -> str:
    return " ".join(value.strip().casefold().split())
