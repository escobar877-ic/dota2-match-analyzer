from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.errors import with_db_error_handling
from app.database import get_db
from app.db.models import Match, PredictionForecast, Team
from app.drafts.draft_service import draft_to_dict
from app.drafts.live_draft_context import load_live_match_context, live_context_to_draft_response
from app.prediction.prediction_service import build_match_prediction
from app.prediction.schemas import FormulaPredictionResponse
from app.prediction.verified_pro_preview import (
    build_verified_pro_preview as _build_verified_pro_preview,
    is_verified_pro_preview_match as _is_verified_pro_preview_match,
)
from app.patches.patch_service import calculate_days_since_patch, get_patch_for_match
from app.rosters.roster_service import (
    get_active_roster,
    get_recent_standins_count,
    get_roster_stability_days,
    get_same_roster_matches_count,
    has_recent_roster_change,
)
from app.schemas.match import MatchDetail, MatchRead
from ml.features.draft_features import build_draft_features


router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("", response_model=list[MatchRead])
def list_matches(
    include_excluded: bool = False,
    include_stale_upcoming: bool = False,
    limit: int = 24,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[Match]:
    safe_limit = max(1, min(100, int(limit)))
    safe_offset = max(0, int(offset))
    statement = (
        select(Match)
        .options(selectinload(Match.team_a), selectinload(Match.team_b))
        .order_by(Match.start_time.desc().nullslast(), Match.id.desc())
    )
    if not include_excluded:
        statement = statement.where(
            Match.is_tier1_match.is_(True),
            Match.team_a.has(Team.is_active_tier1.is_(True)),
            Match.team_b.has(Team.is_active_tier1.is_(True)),
        )
    if not include_stale_upcoming:
        statement = statement.where(
            or_(
                Match.status != "upcoming",
                Match.start_time.is_(None),
                Match.start_time >= datetime.now(timezone.utc),
            )
        )

    statement = statement.offset(safe_offset).limit(safe_limit)

    return with_db_error_handling(
        lambda: list(db.scalars(statement).all())
    )


@router.get("/upcoming")
def list_upcoming_matches(
    q: str | None = None,
    team: str | None = None,
    tournament: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    source: str | None = None,
    verified_only: bool = False,
    prediction_eligible: bool = False,
    analysis_scope: str | None = None,
    include_prediction: bool = False,
    include_finished: bool = False,
    limit: int = 25,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> dict:
    def load() -> dict:
        safe_limit = max(1, min(100, int(limit)))
        safe_offset = max(0, int(offset))
        statuses = ["upcoming", "live", "finished"] if include_finished else ["upcoming", "live"]
        now = datetime.now(timezone.utc)
        base_conditions = [Match.status.in_(statuses)]
        if from_date is None:
            recent_finished_cutoff = now - timedelta(days=21)
            if include_finished:
                base_conditions.append(
                    or_(
                        Match.status == "live",
                        and_(Match.status == "upcoming", Match.start_time >= now),
                        and_(
                            Match.status == "finished",
                            Match.start_time >= recent_finished_cutoff,
                        ),
                    )
                )
            else:
                base_conditions.append(or_(Match.status == "live", Match.start_time >= now))
        else:
            base_conditions.append(Match.start_time >= from_date)
        if to_date:
            base_conditions.append(Match.start_time <= to_date)

        statement = (
            select(Match)
            .options(selectinload(Match.team_a), selectinload(Match.team_b))
            .where(*base_conditions)
            .order_by(Match.start_time.asc().nullslast(), Match.id.asc())
        )
        if source:
            statement = statement.where(Match.external_source == source)
        if tournament:
            statement = statement.where(Match.tournament_name.ilike(f"%{tournament}%"))
        if team:
            team_pattern = f"%{team}%"
            statement = statement.where(or_(Match.team_a.has(Team.name.ilike(team_pattern)), Match.team_b.has(Team.name.ilike(team_pattern))))
        if q:
            pattern = f"%{q}%"
            statement = statement.where(
                or_(
                    Match.tournament_name.ilike(pattern),
                    Match.team_a.has(Team.name.ilike(pattern)),
                    Match.team_b.has(Team.name.ilike(pattern)),
                )
            )

        tournament_statement = select(Match.tournament_name, Match.status).where(*base_conditions)
        if source:
            tournament_statement = tournament_statement.where(Match.external_source == source)
        tournament_options = _build_upcoming_tournament_options(db.execute(tournament_statement).all())

        matches = list(db.scalars(statement).all())
        matches.sort(key=_upcoming_match_sort_key)
        rows = [_upcoming_match_to_dict(match) for match in matches]
        scope_summary = {
            "strict_prediction_count": sum(row["prediction_eligible"] for row in rows),
            "verified_pro_preview_count": sum(row["preview_eligible"] for row in rows),
            "blocked_count": sum(
                not row["prediction_eligible"] and not row["preview_eligible"] for row in rows
            ),
            "training_eligible_count": sum(row["is_training_eligible"] for row in rows),
        }
        if verified_only:
            rows = [row for row in rows if row["verification_status"] == "verified"]
        if prediction_eligible:
            rows = [row for row in rows if row["prediction_eligible"]]
        normalized_scope = (analysis_scope or "all").strip().lower()
        if normalized_scope == "strict":
            rows = [row for row in rows if row["prediction_eligible"]]
        elif normalized_scope == "preview":
            rows = [row for row in rows if row["preview_eligible"]]
        elif normalized_scope == "actionable":
            rows = [row for row in rows if row["prediction_eligible"] or row["preview_eligible"]]
        elif normalized_scope != "all":
            raise HTTPException(status_code=400, detail="analysis_scope must be all, actionable, strict, or preview")
        total = len(rows)
        page_rows = rows[safe_offset : safe_offset + safe_limit]
        if include_prediction:
            match_by_id = {match.id: match for match in matches}
            for row in page_rows:
                _attach_upcoming_decision(row, match_by_id.get(row["id"]), db)
        else:
            for row in page_rows:
                _attach_default_upcoming_decision(row)
        return {
            "items": page_rows,
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
            "scope_summary": scope_summary,
            "tournament_options": tournament_options,
        }

    return with_db_error_handling(load)


def _build_upcoming_tournament_options(rows: list[tuple[str | None, str]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for raw_name, status in rows:
        name = (raw_name or "").strip()
        if not name:
            continue
        key = name.casefold()
        option = grouped.setdefault(
            key,
            {
                "name": name,
                "match_count": 0,
                "live_count": 0,
                "upcoming_count": 0,
            },
        )
        option["match_count"] += 1
        if status == "live":
            option["live_count"] += 1
        elif status == "upcoming":
            option["upcoming_count"] += 1

    return sorted(
        grouped.values(),
        key=lambda option: (-option["live_count"], option["name"].casefold()),
    )


def _upcoming_match_sort_key(match: Match) -> tuple[int, float, int]:
    status_rank = {"live": 0, "upcoming": 1, "finished": 2}.get(match.status, 3)
    start_timestamp = match.start_time.timestamp() if match.start_time else float("inf")
    if match.status == "finished":
        start_timestamp = -start_timestamp
    return status_rank, start_timestamp, match.id


def _upcoming_match_to_dict(match: Match) -> dict:
    verified = (
        match.verification_status == "verified"
        if match.verification_status
        else bool(match.external_source == "pandascore" and match.team_a and match.team_b and match.tournament_name)
    )
    prediction_eligible = bool(
        match.status in {"upcoming", "live"}
        and match.is_tier1_match
        and match.team_a
        and match.team_b
        and match.team_a.is_active_tier1
        and match.team_b.is_active_tier1
    )
    source_prediction_eligible = bool(
        match.status in {"upcoming", "live"}
        and verified
        and match.team_a
        and match.team_b
    )
    preview_eligible = _is_verified_pro_preview_match(match, allow_finished=False)
    return {
        "id": match.id,
        "external_id": match.external_id,
        "source": match.external_source,
        "team_a": {"id": match.team_a.id, "name": match.team_a.name} if match.team_a else None,
        "team_b": {"id": match.team_b.id, "name": match.team_b.name} if match.team_b else None,
        "tournament": match.tournament_name,
        "start_time": match.start_time,
        "status": match.status,
        "format": match.format,
        "dataset_profile": match.dataset_profile or "upcoming",
        "competition_tier": match.competition_tier or ("tier1" if match.is_tier1_match else "unknown"),
        "verification_status": "verified" if verified else "unverified",
        "source_confidence": match.source_confidence or ("high" if verified and match.external_source == "pandascore" else "medium"),
        "source_prediction_eligible": source_prediction_eligible,
        "prediction_eligible": prediction_eligible,
        "preview_eligible": preview_eligible,
        "analysis_mode": (
            "strict_prediction"
            if prediction_eligible
            else "verified_pro_preview"
            if preview_eligible
            else "blocked"
        ),
        "prediction_block_reason": None if prediction_eligible else _upcoming_prediction_block_reason(match),
        "prediction_guard_level": match.prediction_guard_level or ("normal" if match.is_tier1_match else "high"),
        "is_training_eligible": bool(match.is_training_eligible),
    }


def _attach_default_upcoming_decision(row: dict[str, Any]) -> None:
    if row["preview_eligible"]:
        row["decision_status"] = "preview"
        row["decision_reason"] = "Verified pro preview is available; strict Tier 1 prediction remains blocked."
    elif not row["prediction_eligible"]:
        row["decision_status"] = "blocked"
        row["decision_reason"] = row["prediction_block_reason"] or "Prediction is blocked for this match."
    else:
        row["decision_status"] = "watch"
        row["decision_reason"] = "Prediction is eligible. Open match detail for full analysis."
    row["decision_reasons"] = [row["decision_reason"]]
    row["prediction_summary"] = None


def _attach_upcoming_decision(row: dict[str, Any], match: Match | None, db: Session) -> None:
    if match is None:
        _attach_default_upcoming_decision(row)
        return
    if not row["prediction_eligible"]:
        if not row["preview_eligible"]:
            _attach_default_upcoming_decision(row)
            return
        try:
            preview = _build_verified_pro_preview(db, match)
        except Exception as exc:
            row["decision_status"] = "preview"
            row["decision_reason"] = f"Preview could not be generated: {exc.__class__.__name__}."
            row["decision_reasons"] = [row["decision_reason"]]
            row["prediction_summary"] = None
            return
        row["decision_status"] = "preview"
        row["decision_reason"] = "Cautious verified-pro preview; strict Tier 1 prediction remains blocked."
        row["decision_reasons"] = list(preview.confidence_reasons or [])
        row["prediction_summary"] = _prediction_summary(preview)
        return
    try:
        prediction = build_match_prediction(db, match)
    except Exception as exc:
        row["decision_status"] = "watch"
        row["decision_reason"] = f"Prediction could not be generated: {exc.__class__.__name__}."
        row["decision_reasons"] = [row["decision_reason"]]
        row["prediction_summary"] = None
        return
    reasons = list(prediction.confidence_reasons or [])
    if match.status == "live":
        status = "watch"
        reasons = ["Match is already live; this is a pre-match baseline only and market evaluation is disabled."]
    elif prediction.confidence == "low":
        status = "skip"
        reasons = reasons or ["Prediction confidence is low."]
    elif prediction.confidence_guard_applied:
        status = "watch"
        reasons = reasons or ["Confidence guard adjusted this prediction."]
    else:
        status = "needs_odds"
        reasons = ["Prediction is usable; add market odds to check no-vig edge."]
    row["decision_status"] = status
    row["decision_reason"] = reasons[0]
    row["decision_reasons"] = reasons
    row["prediction_summary"] = _prediction_summary(prediction)


def _prediction_summary(prediction) -> dict[str, Any]:
    best_side = "team_a" if prediction.team_a_probability >= prediction.team_b_probability else "team_b"
    return {
        "prediction_type": prediction.prediction_type,
        "team_a_probability": prediction.team_a_probability,
        "team_b_probability": prediction.team_b_probability,
        "probability_unit": getattr(prediction, "probability_unit", None),
        "confidence": prediction.confidence,
        "confidence_score": prediction.confidence_score,
        "confidence_guard_applied": prediction.confidence_guard_applied,
        "best_side": best_side,
        "weight_source": getattr(prediction, "weight_source", None),
        "series_outcomes": prediction.series_outcomes,
    }


def _upcoming_prediction_block_reason(match: Match) -> str:
    reasons = []
    if match.status not in {"upcoming", "live"}:
        reasons.append("not_upcoming")
    if not match.is_tier1_match:
        reasons.append(match.excluded_reason or "not_tier1_match")
    if not match.team_a or not match.team_a.is_active_tier1:
        reasons.append("team_a_not_active_tier1")
    if not match.team_b or not match.team_b.is_active_tier1:
        reasons.append("team_b_not_active_tier1")
    return ",".join(reasons) if reasons else "not_prediction_eligible"


@router.get("/{match_id}", response_model=MatchDetail)
def get_match(match_id: int, db: Session = Depends(get_db)) -> Match:
    match = with_db_error_handling(
        lambda: db.scalar(
            select(Match)
            .options(
                selectinload(Match.team_a),
                selectinload(Match.team_b),
                selectinload(Match.stats),
                selectinload(Match.predictions),
            )
            .where(Match.id == match_id)
        )
    )
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    return match


@router.get("/{match_id}/forecast-history")
def get_match_forecast_history(match_id: int, db: Session = Depends(get_db)) -> dict:
    match = with_db_error_handling(
        lambda: db.scalar(
            select(Match)
            .options(selectinload(Match.team_a), selectinload(Match.team_b))
            .where(Match.id == match_id)
        )
    )
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")

    forecasts = with_db_error_handling(
        lambda: list(
            db.scalars(
                select(PredictionForecast)
                .where(PredictionForecast.match_id == match_id)
                .order_by(PredictionForecast.generated_at.desc(), PredictionForecast.id.desc())
            ).all()
        )
    )
    rows = [
        {
            "id": forecast.id,
            "horizon_bucket": forecast.horizon_bucket,
            "is_primary": forecast.is_primary,
            "generated_at": forecast.generated_at,
            "scheduled_start": forecast.scheduled_start,
            "lead_time_hours": forecast.lead_time_hours,
            "prediction_type": forecast.prediction_type,
            "model_version": forecast.model_version,
            "team_a_probability": forecast.team_a_probability,
            "team_b_probability": forecast.team_b_probability,
            "confidence": forecast.confidence_label,
            "confidence_score": forecast.confidence_score,
            "status": forecast.status,
            "actual_outcome": forecast.actual_outcome,
            "correct": forecast.correct,
            "log_loss": forecast.log_loss,
            "brier_score": forecast.brier_score,
        }
        for forecast in forecasts
    ]
    preferred = next((row for row in rows if row["is_primary"]), rows[0] if rows else None)
    actual_outcome = (
        "draw"
        if match.is_draw
        else "team_a"
        if match.winner_team_id == match.team_a_id
        else "team_b"
        if match.winner_team_id == match.team_b_id
        else None
    )
    return {
        "match_id": match.id,
        "match_status": match.status,
        "actual_outcome": actual_outcome,
        "winner_team_id": match.winner_team_id,
        "winner_team_name": (
            match.team_a.name
            if match.winner_team_id == match.team_a_id and match.team_a
            else match.team_b.name
            if match.winner_team_id == match.team_b_id and match.team_b
            else None
        ),
        "prospective_snapshot_available": bool(rows),
        "preferred_snapshot": preferred,
        "forecasts": rows,
    }


@router.get("/{match_id}/context")
def get_match_context(match_id: int, db: Session = Depends(get_db)) -> dict:
    match = with_db_error_handling(
        lambda: db.scalar(
            select(Match)
            .options(selectinload(Match.team_a), selectinload(Match.team_b))
            .where(Match.id == match_id)
        )
    )
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")

    def build() -> dict:
        patch = get_patch_for_match(db, match.start_time)
        return {
            "patch": {
                "id": patch.id,
                "patch_name": patch.patch_name,
                "patch_version": patch.patch_version,
                "release_date": patch.release_date,
                "is_current": patch.is_current,
            }
            if patch
            else None,
            "days_since_patch": calculate_days_since_patch(db, match.start_time),
            "is_current_patch": bool(patch and patch.is_current),
            "teams": {
                "team_a": _team_context(db, match.team_a_id, match.start_time),
                "team_b": _team_context(db, match.team_b_id, match.start_time),
            },
        }

    return with_db_error_handling(build)


@router.get("/{match_id}/draft")
def get_match_draft(match_id: int, db: Session = Depends(get_db)) -> dict:
    match = with_db_error_handling(
        lambda: db.scalar(
            select(Match)
            .options(selectinload(Match.team_a), selectinload(Match.team_b))
            .where(Match.id == match_id)
        )
    )
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")

    def build() -> dict:
        stored_draft = draft_to_dict(db, match)
        if stored_draft["draft_available"] or match.status != "live":
            return stored_draft
        live_context = load_live_match_context(match.id)
        if live_context is None:
            return stored_draft
        return live_context_to_draft_response(match, live_context)

    return with_db_error_handling(build)


@router.get("/{match_id}/draft/features")
def get_match_draft_features(match_id: int, db: Session = Depends(get_db)) -> dict:
    match = with_db_error_handling(
        lambda: db.scalar(
            select(Match)
            .options(selectinload(Match.team_a), selectinload(Match.team_b))
            .where(Match.id == match_id)
        )
    )
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    return with_db_error_handling(lambda: {"experimental": True, "features": build_draft_features(db, match)})


@router.get("/{match_id}/prediction", response_model=FormulaPredictionResponse)
def get_match_prediction(match_id: int, db: Session = Depends(get_db)) -> FormulaPredictionResponse | JSONResponse:
    match = with_db_error_handling(
        lambda: db.scalar(
            select(Match)
            .options(selectinload(Match.team_a), selectinload(Match.team_b))
            .where(Match.id == match_id)
        )
    )
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    if not _is_tier1_analysis_match(match):
        return JSONResponse(
            status_code=403,
            content={
                "error": "This match is excluded from analysis because it is not Tier 1.",
                "excluded_reason": _prediction_excluded_reason(match),
            },
        )

    return with_db_error_handling(lambda: build_match_prediction(db, match))


@router.get("/{match_id}/analysis-preview", response_model=FormulaPredictionResponse)
def get_match_analysis_preview(match_id: int, db: Session = Depends(get_db)) -> FormulaPredictionResponse | JSONResponse:
    match = with_db_error_handling(
        lambda: db.scalar(
            select(Match)
            .options(selectinload(Match.team_a), selectinload(Match.team_b))
            .where(Match.id == match_id)
        )
    )
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    if _is_tier1_analysis_match(match):
        return with_db_error_handling(lambda: build_match_prediction(db, match))
    if not _is_verified_pro_preview_match(match, allow_finished=True):
        return JSONResponse(
            status_code=403,
            content={
                "error": (
                    "Analysis preview requires a verified pro match in an allowlisted tournament "
                    "with known teams."
                )
            },
        )

    def build_preview() -> FormulaPredictionResponse:
        return _build_verified_pro_preview(db, match)

    return with_db_error_handling(build_preview)


def _is_tier1_analysis_match(match: Match) -> bool:
    return bool(
        match.is_tier1_match
        and match.team_a.is_active_tier1
        and match.team_b.is_active_tier1
    )


def _prediction_excluded_reason(match: Match) -> str:
    reasons = []
    if match.excluded_reason:
        reasons.append(match.excluded_reason)
    if not match.team_a.is_active_tier1:
        reasons.append("team_a_not_active_tier1")
    if not match.team_b.is_active_tier1:
        reasons.append("team_b_not_active_tier1")
    return ",".join(reasons) if reasons else "not_tier1_match"


def _team_context(db: Session, team_id: int, at_date) -> dict:
    roster = get_active_roster(db, team_id, at_date)
    roster_count = len({entry.player_id for entry in roster})
    stability_known = any(entry.start_date is not None for entry in roster)
    return {
        "team_id": team_id,
        "roster_count": roster_count,
        "roster_known": roster_count == 5,
        "roster_ambiguous": roster_count > 5,
        "roster_stability_known": stability_known,
        "roster_stability_days": (
            get_roster_stability_days(db, team_id, at_date) if stability_known else None
        ),
        "same_roster_matches_count": get_same_roster_matches_count(db, team_id, at_date),
        "has_recent_roster_change": has_recent_roster_change(db, team_id, at_date),
        "recent_standins_count": get_recent_standins_count(db, team_id, at_date),
    }
