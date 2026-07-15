from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Match
from app.prediction.ensemble_prediction_service import (
    try_predict_with_ensemble,
)
from app.prediction.feature_snapshot import VERIFIED_PRO_HISTORY_SCOPE
from app.prediction.schemas import FormulaPredictionResponse
from app.prediction.series_outcomes import attach_series_outcomes


def build_verified_pro_preview(db: Session, match: Match) -> FormulaPredictionResponse:
    ensemble_result = try_predict_with_ensemble(
        db,
        match,
        formula_history_scope=VERIFIED_PRO_HISTORY_SCOPE,
        allow_verified_pro_ml=True,
    )
    uses_ensemble = isinstance(ensemble_result, FormulaPredictionResponse)
    prediction = (
        ensemble_result
        if uses_ensemble
        else ensemble_result.formula_prediction
    )
    prediction = attach_series_outcomes(prediction, match.format)
    prediction.prediction_type = "verified_pro_preview"
    prediction.model_version = (
        "verified_pro_ensemble_preview_v1"
        if uses_ensemble
        else "formula_verified_pro_preview_v2"
    )
    prediction.confidence = "low"
    prediction.confidence_score = min(prediction.confidence_score, 0.45)
    prediction.fallback_used = True
    prediction.fallback_reason = "verified_pro_not_strict_tier1"
    prediction.warning = (
        "Verified pro preview only. This match is not strict Tier 1 prediction eligible, "
        "so use this as cautious context, not a main prediction signal."
    )
    prediction.confidence_guard_applied = True
    prediction.confidence_reasons = [
        "Match is verified by source but blocked by strict Tier 1 team allowlist.",
        "Preview uses real verified pro history before match start and is isolated from strict metrics and promotion.",
    ]
    if uses_ensemble:
        prediction.confidence_reasons.append(
            "Guarded Formula, Elo and available local ML components are combined only inside this preview."
        )
    context = prediction.analytics_context or {}
    team_a_matches = int((context.get("team_a") or {}).get("matches_count") or 0)
    team_b_matches = int((context.get("team_b") or {}).get("matches_count") or 0)
    if isinstance(prediction.explanation, list):
        prediction.explanation.append(
            f"Verified pro history sample: {team_a_matches} prior matches for Team A and "
            f"{team_b_matches} for Team B."
        )
    elif isinstance(prediction.explanation, dict):
        prediction.explanation["preview_scope"] = (
            "verified_pro_only_not_used_in_main_prediction"
        )
    if min(team_a_matches, team_b_matches) < 10:
        prediction.confidence_reasons.append(
            "At least one team has fewer than 10 verified prior matches."
        )
    if int(context.get("head_to_head_matches") or 0) == 0:
        prediction.confidence_reasons.append(
            "No verified head-to-head history is available before match start."
        )
    team_a_context = context.get("team_a") or {}
    team_b_context = context.get("team_b") or {}
    if min(
        int(team_a_context.get("roster_count") or 0),
        int(team_b_context.get("roster_count") or 0),
    ) < 5:
        prediction.confidence_reasons.append(
            "Roster data is incomplete, so roster advantage is neutralized."
        )
    if max(
        int(team_a_context.get("roster_count") or 0),
        int(team_b_context.get("roster_count") or 0),
    ) > 5:
        prediction.confidence_reasons.append(
            "At least one source roster is ambiguous because it lists more than five active players."
        )
    if min(
        int(team_a_context.get("stats_count") or 0),
        int(team_b_context.get("stats_count") or 0),
    ) == 0:
        prediction.confidence_reasons.append(
            "Detailed team statistics are incomplete for this source history."
        )
    if match.status == "live":
        prediction.confidence_reasons.append(
            "Live score and in-game state are not used; this remains a pre-match baseline."
        )
        prediction.warning = f"{prediction.warning} Live score and in-game state are not modeled."
    if match.status == "finished":
        prediction.confidence_reasons.append("Match has already finished.")
    return prediction


def is_verified_pro_preview_match(match: Match, *, allow_finished: bool) -> bool:
    allowed_statuses = {"upcoming", "live", "finished"} if allow_finished else {"upcoming", "live"}
    if match.status not in allowed_statuses:
        return False
    if match.verification_status != "verified" or match.source_confidence not in {"high", "medium"}:
        return False
    if match.competition_tier != "pro" or not match.team_a or not match.team_b:
        return False
    if not _known_team_name(match.team_a.name) or not _known_team_name(match.team_b.name):
        return False
    block_text = ",".join(
        value for value in (match.excluded_reason, match.prediction_block_reason) if value
    ).lower()
    if "tournament_not_tier1" in block_text or "qualifier" in block_text:
        return False
    if not allow_finished and bool(match.is_training_eligible):
        return False
    return True


def _known_team_name(name: str | None) -> bool:
    normalized = " ".join((name or "").strip().lower().split())
    return normalized not in {"", "tbd", "unknown", "to be determined"} and not normalized.startswith(
        ("winner of", "loser of")
    )
