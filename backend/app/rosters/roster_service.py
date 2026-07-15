from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Match, Team, TeamRoster
from app.ratings.team_identity import resolve_team_identity_ids


def get_active_roster(db: Session, team_id: int, at_date: datetime | None = None) -> list[TeamRoster]:
    at_date = _normalize_at_date(at_date)
    identity_ids = resolve_team_identity_ids(db, team_id)
    statement = select(TeamRoster).where(TeamRoster.team_id.in_(identity_ids))
    if at_date is None:
        statement = statement.where(TeamRoster.is_active.is_(True))
    else:
        statement = statement.where(
            (TeamRoster.start_date.is_(None) | (TeamRoster.start_date <= at_date)),
            (TeamRoster.end_date.is_(None) | (TeamRoster.end_date > at_date)),
        )
    candidates = list(
        db.scalars(statement.order_by(TeamRoster.role.asc().nullslast(), TeamRoster.id.asc())).all()
    )
    return _select_roster_identity(db, team_id, candidates)


def get_roster_stability_days(db: Session, team_id: int, at_date: datetime | None) -> int:
    at_date = _normalize_at_date(at_date)
    if at_date is None:
        return 0
    roster = get_active_roster(db, team_id, at_date)
    starts = [entry.start_date for entry in roster if entry.start_date is not None]
    if not starts:
        return 0
    latest_start = max(starts)
    return max(0, (at_date.date() - latest_start.date()).days)


def get_same_roster_matches_count(db: Session, team_id: int, at_date: datetime | None) -> int:
    at_date = _normalize_at_date(at_date)
    if at_date is None:
        return 0
    roster = get_active_roster(db, team_id, at_date)
    starts = [entry.start_date for entry in roster if entry.start_date is not None]
    if not starts:
        return 0
    latest_start = max(starts)
    identity_ids = resolve_team_identity_ids(db, team_id)
    statement = (
        select(func.count(func.distinct(Match.id)))
        .select_from(Match)
        .where(
            Match.status == "finished",
            Match.winner_team_id.is_not(None),
            Match.is_tier1_match.is_(True),
            Match.start_time.is_not(None),
            Match.start_time < at_date,
            Match.start_time >= latest_start,
            ((Match.team_a_id.in_(identity_ids)) | (Match.team_b_id.in_(identity_ids))),
        )
    )
    team = db.get(Team, team_id)
    if team and team.external_source == "dev_seed":
        statement = statement.where(Match.external_source == "dev_seed")
    else:
        statement = statement.where(
            or_(Match.external_source.is_(None), Match.external_source.notin_(["dev_seed", "demo"]))
        )
    return db.scalar(statement) or 0


def has_recent_roster_change(db: Session, team_id: int, at_date: datetime | None, days: int = 30) -> bool:
    at_date = _normalize_at_date(at_date)
    if at_date is None:
        return False
    roster = get_active_roster(db, team_id, at_date)
    if not roster:
        return False
    roster_team_id = roster[0].team_id
    cutoff = at_date - timedelta(days=days)
    return (
        db.scalar(
            select(TeamRoster.id)
            .where(
                TeamRoster.team_id == roster_team_id,
                TeamRoster.start_date.is_not(None),
                TeamRoster.start_date < at_date,
                TeamRoster.start_date >= cutoff,
            )
            .limit(1)
        )
        is not None
    )


def get_recent_standins_count(db: Session, team_id: int, at_date: datetime | None) -> int:
    at_date = _normalize_at_date(at_date)
    if at_date is None:
        return 0
    roster = get_active_roster(db, team_id, at_date)
    if not roster:
        return 0
    roster_team_id = roster[0].team_id
    cutoff = at_date - timedelta(days=30)
    return len(
        list(
            db.scalars(
                select(TeamRoster.id).where(
                    TeamRoster.team_id == roster_team_id,
                    TeamRoster.source == "standin",
                    TeamRoster.start_date.is_not(None),
                    TeamRoster.start_date < at_date,
                    TeamRoster.start_date >= cutoff,
                )
            )
        )
    )


def _normalize_at_date(at_date: datetime | None) -> datetime | None:
    if at_date is None:
        return None
    return at_date if at_date.tzinfo else at_date.replace(tzinfo=timezone.utc)


def _select_roster_identity(
    db: Session,
    requested_team_id: int,
    candidates: list[TeamRoster],
) -> list[TeamRoster]:
    if not candidates:
        return []
    grouped: dict[int, list[TeamRoster]] = {}
    for entry in candidates:
        grouped.setdefault(entry.team_id, []).append(entry)

    requested_team = db.get(Team, requested_team_id)
    exact = grouped.get(requested_team_id, [])
    if any(entry.start_date is not None for entry in exact):
        return exact

    team_sources = {
        team_id: source
        for team_id, source in db.execute(
            select(Team.id, Team.external_source).where(Team.id.in_(grouped))
        )
    }
    if requested_team and requested_team.external_source not in {"dev_seed", "demo"}:
        grouped = {
            roster_team_id: entries
            for roster_team_id, entries in grouped.items()
            if team_sources.get(roster_team_id) not in {"dev_seed", "demo"}
        }
        if not grouped:
            return exact

    def roster_score(item: tuple[int, list[TeamRoster]]) -> tuple[int, int, datetime, int]:
        roster_team_id, entries = item
        dated_starts = [entry.start_date for entry in entries if entry.start_date is not None]
        latest_start = max(dated_starts) if dated_starts else datetime.min.replace(tzinfo=timezone.utc)
        source = team_sources.get(roster_team_id)
        is_real_source = source not in {"dev_seed", "demo"}
        return (bool(dated_starts), is_real_source, latest_start, len(entries))

    selected_team_id, selected = max(grouped.items(), key=roster_score)
    if exact and not any(entry.start_date is not None for entry in selected):
        return exact
    return selected
