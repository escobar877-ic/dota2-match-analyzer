import math

from sqlalchemy.orm import Session

from app.db.models import Match, Prediction
from app.prediction.explanation_builder import build_explanation
from app.prediction.fallback import MAX_PROBABILITY, MIN_PROBABILITY, PREDICTION_WARNING
from app.prediction.feature_snapshot import TIER1_HISTORY_SCOPE, build_match_feature_snapshot
from app.prediction.schemas import FormulaPredictionResponse, PredictionFactors


MODEL_TYPE = "formula"
MODEL_VERSION = "formula_v1"


class FormulaPredictionEngine:
    weights = {
        "recent_form": 0.35,
        "team_rating": 0.25,
        "head_to_head": 0.15,
        "hero_pool": 0.15,
        "roster_stability": 0.10,
    }

    def predict_and_save(
        self,
        db: Session,
        match: Match,
        *,
        fallback_used: bool = False,
        fallback_reason: str | None = None,
        data_freshness: dict[str, str | None] | None = None,
    ) -> FormulaPredictionResponse:
        prediction = self.predict(db, match)
        prediction.fallback_used = fallback_used
        prediction.fallback_reason = fallback_reason
        prediction.data_freshness = data_freshness
        db_prediction = Prediction(
            match_id=match.id,
            team_a_probability=prediction.team_a_probability,
            team_b_probability=prediction.team_b_probability,
            confidence=prediction.confidence_score,
            explanation_json={
                "confidence": prediction.confidence,
                "factors": prediction.factors.model_dump(),
                "explanation": prediction.explanation,
                "warning": prediction.warning,
                "fallback_used": prediction.fallback_used,
                "fallback_reason": prediction.fallback_reason,
                "data_freshness": prediction.data_freshness,
            },
            model_type=prediction.prediction_type,
            model_version=prediction.model_version,
        )
        db.add(db_prediction)
        db.commit()
        return prediction

    def predict(
        self,
        db: Session,
        match: Match,
        *,
        history_scope: str = TIER1_HISTORY_SCOPE,
    ) -> FormulaPredictionResponse:
        snapshot = build_match_feature_snapshot(db, match, history_scope=history_scope)

        roster_difference = snapshot.team_a.roster_stability - snapshot.team_b.roster_stability
        if history_scope != TIER1_HISTORY_SCOPE and (
            snapshot.team_a.roster_count != 5 or snapshot.team_b.roster_count != 5
        ):
            roster_difference = 0.0

        factors = PredictionFactors(
            recent_form=self.weights["recent_form"] * (snapshot.team_a.recent_form - snapshot.team_b.recent_form),
            team_rating=self.weights["team_rating"] * (snapshot.team_a.rating - snapshot.team_b.rating),
            head_to_head=self.weights["head_to_head"] * snapshot.head_to_head,
            hero_pool=self.weights["hero_pool"] * (snapshot.team_a.hero_pool - snapshot.team_b.hero_pool),
            roster_stability=self.weights["roster_stability"] * roster_difference,
        )

        score = sum(factors.model_dump().values())
        confidence_score = self._confidence_score(snapshot)
        team_a_probability = self._score_to_probability(score, confidence_score)
        team_b_probability = round(1 - team_a_probability, 4)
        team_a_probability = round(1 - team_b_probability, 4)

        analytics_context = None
        if history_scope != TIER1_HISTORY_SCOPE:
            analytics_context = {
                "history_scope": history_scope,
                "cutoff": match.start_time.isoformat() if match.start_time else None,
                "uses_only_past_matches": match.start_time is not None,
                "identity_resolution": "exact_normalized_name_or_configured_alias",
                "dev_seed_included": False,
                "head_to_head_matches": snapshot.head_to_head_count,
                "team_a": _team_analytics(snapshot.team_a),
                "team_b": _team_analytics(snapshot.team_b),
            }

        return FormulaPredictionResponse(
            match_id=str(match.id),
            prediction_type=MODEL_TYPE,
            model_version=MODEL_VERSION,
            team_a_probability=team_a_probability,
            team_b_probability=team_b_probability,
            confidence=self._confidence_label(confidence_score),
            confidence_score=confidence_score,
            factors=PredictionFactors(
                recent_form=round(factors.recent_form, 4),
                team_rating=round(factors.team_rating, 4),
                head_to_head=round(factors.head_to_head, 4),
                hero_pool=round(factors.hero_pool, 4),
                roster_stability=round(factors.roster_stability, 4),
            ),
            explanation=build_explanation(factors),
            warning=PREDICTION_WARNING,
            analytics_context=analytics_context,
        )

    def _score_to_probability(self, score: float, confidence_score: float) -> float:
        raw_probability = 1 / (1 + math.exp(-4.0 * score))

        # Low data confidence shrinks predictions toward 50/50.
        shrink = 0.35 + confidence_score * 0.65
        probability = 0.5 + (raw_probability - 0.5) * shrink
        probability = max(MIN_PROBABILITY, min(MAX_PROBABILITY, probability))
        return round(probability, 4)

    def _confidence_score(self, snapshot) -> float:
        team_a_data = min(snapshot.team_a.matches_count, 20) / 20
        team_b_data = min(snapshot.team_b.matches_count, 20) / 20
        h2h_data = min(snapshot.head_to_head_count, 6) / 6
        roster_data = (
            _roster_data_quality(snapshot.team_a.roster_count) * 0.5
            + _roster_data_quality(snapshot.team_b.roster_count) * 0.5
        )
        stats_data = min(snapshot.team_a.stats_count, 10) / 10 * 0.5 + min(snapshot.team_b.stats_count, 10) / 10 * 0.5

        score = team_a_data * 0.28 + team_b_data * 0.28 + h2h_data * 0.16 + roster_data * 0.14 + stats_data * 0.14
        return round(max(0.15, min(0.9, score)), 2)

    def _confidence_label(self, confidence_score: float) -> str:
        if confidence_score < 0.45:
            return "low"
        if confidence_score < 0.7:
            return "medium"
        return "high"


def _team_analytics(team) -> dict:
    return {
        "elo_rating": team.elo_rating,
        "glicko_rating": team.glicko_rating,
        "rating_uncertainty": team.rating_uncertainty,
        "recent_form": round(team.recent_form, 4),
        "matches_count": team.matches_count,
        "roster_count": team.roster_count,
        "stats_count": team.stats_count,
    }


def _roster_data_quality(roster_count: int) -> float:
    if roster_count == 5:
        return 1.0
    if roster_count < 5:
        return max(0.0, roster_count / 5)
    return 0.6
