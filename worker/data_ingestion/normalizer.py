from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any

from app.tier_filter.tier1_config_loader import load_tier1_config


@dataclass(frozen=True)
class NormalizedTeam:
    external_source: str
    external_id: str
    name: str
    logo_url: str | None = None
    country: str | None = None
    region: str | None = None


@dataclass(frozen=True)
class NormalizedPlayer:
    external_source: str
    external_id: str
    nickname: str
    real_name: str | None = None
    team_external_id: str | None = None
    role: str | None = None
    country: str | None = None


@dataclass(frozen=True)
class NormalizedMatch:
    external_source: str
    external_id: str
    team_a_external_id: str
    team_b_external_id: str
    team_a_name: str | None = None
    team_b_name: str | None = None
    tournament_name: str | None = None
    tournament_tier: str | None = None
    start_time: datetime | None = None
    format: str | None = None
    status: str = "upcoming"
    winner_team_external_id: str | None = None
    raw_team_a: str | None = None
    raw_team_b: str | None = None
    raw_team_a_id: str | None = None
    raw_team_b_id: str | None = None
    raw_tournament: str | None = None
    raw_tournament_id: str | None = None
    is_draw: bool = False


def normalize_opendota_teams(payload: Any) -> list[NormalizedTeam]:
    if not isinstance(payload, list):
        return []

    teams: list[NormalizedTeam] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        external_id = item.get("team_id")
        name = item.get("name")
        if external_id is None or not name:
            continue
        teams.append(
            NormalizedTeam(
                external_source="opendota",
                external_id=str(external_id),
                name=normalize_team_name(str(name)),
                logo_url=item.get("logo_url"),
            )
        )
    return teams


def normalize_opendota_players(payload: Any, team_external_id: str | None = None) -> list[NormalizedPlayer]:
    if not isinstance(payload, list):
        return []

    players: list[NormalizedPlayer] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        external_id = item.get("account_id")
        nickname = item.get("name") or item.get("personaname")
        if external_id is None or not nickname:
            continue
        players.append(
            NormalizedPlayer(
                external_source="opendota",
                external_id=str(external_id),
                nickname=str(nickname),
                team_external_id=team_external_id,
            )
        )
    return players


def normalize_opendota_matches(payload: Any) -> list[NormalizedMatch]:
    if not isinstance(payload, list):
        return []

    matches: list[NormalizedMatch] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        match_id = item.get("match_id")
        radiant_team_id = item.get("radiant_team_id")
        dire_team_id = item.get("dire_team_id")
        league_id = item.get("leagueid") or item.get("league_id")
        if match_id is None or radiant_team_id is None or dire_team_id is None:
            continue

        radiant_win = item.get("radiant_win")
        winner_external_id = None
        status = "finished" if radiant_win is not None else "upcoming"
        if radiant_win is True:
            winner_external_id = str(radiant_team_id)
        elif radiant_win is False:
            winner_external_id = str(dire_team_id)

        radiant_team = item.get("radiant_team") if isinstance(item.get("radiant_team"), dict) else {}
        dire_team = item.get("dire_team") if isinstance(item.get("dire_team"), dict) else {}
        league = item.get("league") if isinstance(item.get("league"), dict) else {}
        radiant_name = item.get("radiant_name") or radiant_team.get("name")
        dire_name = item.get("dire_name") or dire_team.get("name")
        league_name = item.get("league_name") or league.get("name")

        matches.append(
            NormalizedMatch(
                external_source="opendota",
                external_id=str(match_id),
                team_a_external_id=str(radiant_team_id),
                team_b_external_id=str(dire_team_id),
                team_a_name=normalize_team_name(radiant_name),
                team_b_name=normalize_team_name(dire_name),
                tournament_name=normalize_tournament_name(league_name),
                tournament_tier=None,
                start_time=normalize_datetime(item.get("start_time")),
                format=None,
                status=status,
                winner_team_external_id=winner_external_id,
                raw_team_a=str(radiant_name) if radiant_name is not None else None,
                raw_team_b=str(dire_name) if dire_name is not None else None,
                raw_team_a_id=str(radiant_team_id),
                raw_team_b_id=str(dire_team_id),
                raw_tournament=str(league_name) if league_name is not None else None,
                raw_tournament_id=str(league_id) if league_id is not None else None,
            )
        )
    return matches


def normalize_pandascore_teams(payload: Any) -> list[NormalizedTeam]:
    if not isinstance(payload, list):
        return []

    teams: list[NormalizedTeam] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        external_id = item.get("id")
        name = item.get("name")
        if external_id is None or not name:
            continue
        teams.append(
            NormalizedTeam(
                external_source="pandascore",
                external_id=str(external_id),
                name=normalize_team_name(str(name)),
                logo_url=item.get("image_url"),
                country=item.get("location"),
            )
        )
    return teams


def normalize_pandascore_players(payload: Any) -> list[NormalizedPlayer]:
    if not isinstance(payload, list):
        return []

    players: list[NormalizedPlayer] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        external_id = item.get("id")
        nickname = item.get("name") or item.get("first_name")
        if external_id is None or not nickname:
            continue
        current_team = item.get("current_team") if isinstance(item.get("current_team"), dict) else {}
        players.append(
            NormalizedPlayer(
                external_source="pandascore",
                external_id=str(external_id),
                nickname=str(nickname),
                real_name=_join_name(item.get("first_name"), item.get("last_name")),
                team_external_id=str(current_team["id"]) if current_team.get("id") is not None else None,
                role=item.get("role"),
                country=item.get("nationality"),
            )
        )
    return players


def normalize_pandascore_matches(payload: Any) -> list[NormalizedMatch]:
    if not isinstance(payload, list):
        return []

    matches: list[NormalizedMatch] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        external_id = item.get("id")
        opponents = item.get("opponents")
        if external_id is None or not isinstance(opponents, list) or len(opponents) < 2:
            continue

        team_a = _opponent_team(opponents[0])
        team_b = _opponent_team(opponents[1])
        if not team_a or not team_b:
            continue

        status = normalize_match_status(item.get("status"))
        winner = item.get("winner") if isinstance(item.get("winner"), dict) else None
        winner_external_id, is_draw = _pandascore_result(match=item, team_a_id=team_a["id"], team_b_id=team_b["id"], winner=winner, status=status)
        league = item.get("league") if isinstance(item.get("league"), dict) else {}
        serie = item.get("serie") if isinstance(item.get("serie"), dict) else {}

        matches.append(
            NormalizedMatch(
                external_source="pandascore",
                external_id=str(external_id),
                team_a_external_id=str(team_a["id"]),
                team_b_external_id=str(team_b["id"]),
                team_a_name=normalize_team_name(team_a.get("name")),
                team_b_name=normalize_team_name(team_b.get("name")),
                tournament_name=normalize_tournament_name(league.get("name") or serie.get("full_name")),
                tournament_tier=None,
                start_time=normalize_datetime(item.get("begin_at") or item.get("scheduled_at")),
                format=normalize_match_format(item.get("number_of_games")),
                status=status,
                winner_team_external_id=winner_external_id,
                raw_team_a=str(team_a.get("name")) if team_a.get("name") is not None else None,
                raw_team_b=str(team_b.get("name")) if team_b.get("name") is not None else None,
                raw_team_a_id=str(team_a["id"]),
                raw_team_b_id=str(team_b["id"]),
                raw_tournament=str(league.get("name") or serie.get("full_name"))
                if league.get("name") or serie.get("full_name")
                else None,
                raw_tournament_id=str(league.get("id")) if league.get("id") is not None else None,
                is_draw=is_draw,
            )
        )
    return matches


def _pandascore_result(
    *,
    match: dict[str, Any],
    team_a_id: Any,
    team_b_id: Any,
    winner: dict[str, Any] | None,
    status: str,
) -> tuple[str | None, bool]:
    winner_id = winner.get("id") if winner and winner.get("id") is not None else match.get("winner_id")
    if winner_id is not None:
        return str(winner_id), False

    results = match.get("results")
    if status != "finished" or not isinstance(results, list):
        return None, False

    scores: dict[str, int] = {}
    for result in results:
        if not isinstance(result, dict) or result.get("team_id") is None:
            continue
        try:
            scores[str(result["team_id"])] = int(result.get("score") or 0)
        except (TypeError, ValueError):
            continue

    team_a_score = scores.get(str(team_a_id))
    team_b_score = scores.get(str(team_b_id))
    if team_a_score is None or team_b_score is None:
        return None, False
    if team_a_score == team_b_score:
        return None, True
    return (str(team_a_id) if team_a_score > team_b_score else str(team_b_id)), False


def normalize_stratz_teams(payload: Any) -> list[NormalizedTeam]:
    records = _unwrap_stratz_records(payload, "teams")
    teams: list[NormalizedTeam] = []
    for item in records:
        external_id = item.get("id")
        name = item.get("name") or item.get("tag")
        if external_id is None or not name:
            continue
        teams.append(
            NormalizedTeam(
                external_source="stratz",
                external_id=str(external_id),
                name=normalize_team_name(str(name)),
                logo_url=item.get("logo"),
                region=item.get("region"),
            )
        )
    return teams


def normalize_stratz_matches(payload: Any) -> list[NormalizedMatch]:
    records = payload if isinstance(payload, list) else _unwrap_stratz_records(payload, "matches")
    matches: list[NormalizedMatch] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        external_id = item.get("id")
        radiant_team = item.get("radiantTeam") if isinstance(item.get("radiantTeam"), dict) else {}
        dire_team = item.get("direTeam") if isinstance(item.get("direTeam"), dict) else {}
        radiant_id = radiant_team.get("id") or item.get("radiantTeamId")
        dire_id = dire_team.get("id") or item.get("direTeamId")
        if external_id is None or radiant_id is None or dire_id is None:
            continue
        did_radiant_win = item.get("didRadiantWin")
        winner = None
        if did_radiant_win is True:
            winner = str(radiant_id)
        elif did_radiant_win is False:
            winner = str(dire_id)
        status = normalize_match_status(item.get("status"))
        if winner:
            status = "finished"
        elif item.get("endDateTime") or item.get("durationSeconds") or item.get("duration"):
            status = "finished"
        league = item.get("league") if isinstance(item.get("league"), dict) else {}
        matches.append(
            NormalizedMatch(
                external_source="stratz",
                external_id=str(external_id),
                team_a_external_id=str(radiant_id),
                team_b_external_id=str(dire_id),
                team_a_name=normalize_team_name(radiant_team.get("name")),
                team_b_name=normalize_team_name(dire_team.get("name")),
                tournament_name=normalize_tournament_name(league.get("name")),
                start_time=normalize_datetime(item.get("startDateTime")),
                status=status,
                winner_team_external_id=winner,
                raw_team_a=str(radiant_team.get("name")) if radiant_team.get("name") is not None else None,
                raw_team_b=str(dire_team.get("name")) if dire_team.get("name") is not None else None,
                raw_team_a_id=str(radiant_id),
                raw_team_b_id=str(dire_id),
                raw_tournament=str(league.get("name")) if league.get("name") is not None else None,
                raw_tournament_id=str(league.get("id")) if league.get("id") is not None else None,
            )
        )
    return matches


def normalize_team_name(value: Any) -> str:
    cleaned = _clean_name(value)
    if not cleaned:
        return ""
    aliases = _team_aliases()
    return aliases.get(normalize_lookup_key(cleaned), cleaned)


def normalize_tournament_name(value: Any) -> str | None:
    cleaned = _clean_name(value)
    if not cleaned:
        return None
    if re.fullmatch(r"the international\s+20\d{2}", normalize_lookup_key(cleaned)):
        return "The International"
    aliases = _tournament_aliases()
    return aliases.get(normalize_lookup_key(cleaned), cleaned)


def normalize_match_format(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, int):
        return f"BO{value}"
    text = str(value).strip()
    if not text:
        return None
    normalized = normalize_lookup_key(text)
    match = re.search(r"(?:bo|best of)\s*(\d+)", normalized)
    if match:
        return f"BO{match.group(1)}"
    if normalized.isdigit():
        return f"BO{normalized}"
    return text.upper()


def normalize_match_status(value: Any) -> str:
    normalized = normalize_lookup_key(value or "")
    if normalized in {"not started", "not started yet", "scheduled", "upcoming", "pending"}:
        return "upcoming"
    if normalized in {"running", "live", "in progress", "started"}:
        return "live"
    if normalized in {"finished", "closed", "complete", "completed", "ended"}:
        return "finished"
    return "upcoming"


def normalize_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return _unix_to_datetime(value)
    if isinstance(value, str) and value.strip().isdigit():
        return _unix_to_datetime(value)
    return _iso_to_datetime(value)


def normalize_lookup_key(value: Any) -> str:
    text = _clean_name(value)
    text = text.casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _unix_to_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _iso_to_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _best_of_to_format(value: Any) -> str | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return f"BO{number}"


def _normalize_status(value: Any) -> str:
    return normalize_match_status(value)


def _join_name(first_name: Any, last_name: Any) -> str | None:
    parts = [str(part) for part in [first_name, last_name] if part]
    return " ".join(parts) if parts else None


def _opponent_team(opponent: Any) -> dict[str, Any] | None:
    if not isinstance(opponent, dict):
        return None
    team = opponent.get("opponent")
    return team if isinstance(team, dict) and team.get("id") is not None else None


def _unwrap_stratz_records(payload: Any, key: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    records = data.get(key)
    return records if isinstance(records, list) else []


def _clean_name(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("–", "-").replace("—", "-").replace("_", " ")
    return " ".join(text.split())


def _team_aliases() -> dict[str, str]:
    config = load_tier1_config()
    aliases = {}
    for team in config.teams:
        if not team.active:
            continue
        aliases[normalize_lookup_key(team.name)] = team.name
        for alias in team.aliases:
            aliases[normalize_lookup_key(alias)] = team.name
    return aliases


def _tournament_aliases() -> dict[str, str]:
    config = load_tier1_config()
    aliases = {}
    for tournament in config.tournaments:
        if not tournament.active:
            continue
        aliases[normalize_lookup_key(tournament.name)] = tournament.name
        for alias in tournament.aliases:
            aliases[normalize_lookup_key(alias)] = tournament.name
    return aliases
