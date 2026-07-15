from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.matches import get_match_prediction
from app.betting.schemas import (
    MarketEvaluationRequest,
    MarketEvaluationResponse,
    PaperBetRead,
)
from app.betting.paper_bet_settlement import build_paper_bet_summary
from app.betting.value_service import evaluate_market
from app.database import get_db
from app.db.models import MarketOddsSnapshot, Match, PaperBet
from app.prediction.schemas import FormulaPredictionResponse
from ml.config import ML_ARTIFACT_DIR
from worker.odds_ingestion.sportsgameodds_client import SportsGameOddsClient


router = APIRouter(tags=["betting-paper"])
ODDS_SYNC_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "odds_sync_report.json"


@router.get("/betting/odds-sources/status")
def odds_sources_status() -> dict:
    aggregator = SportsGameOddsClient().get_status()
    return {
        "sources": {
            "pandascore_statistics": {
                "enabled": False,
                "reason": "PandaScore Statistics API does not include bookmaker odds.",
                "setup_hint": "A separate PandaScore Odds product is required.",
            },
            "sportsgameodds": aggregator,
        },
        "credentials_exposed": False,
    }


@router.get("/betting/odds-sync-report")
def odds_sync_report() -> dict:
    if not ODDS_SYNC_REPORT_PATH.exists():
        return {
            "status": "missing",
            "message": "Run bash scripts/sync_market_odds.sh",
        }
    try:
        return json.loads(ODDS_SYNC_REPORT_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {
            "status": "invalid",
            "message": "Regenerate the odds sync report.",
        }


@router.post(
    "/matches/{match_id}/odds/evaluate",
    response_model=MarketEvaluationResponse,
)
def evaluate_odds(
    match_id: int,
    payload: MarketEvaluationRequest,
    db: Session = Depends(get_db),
) -> MarketEvaluationResponse:
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    now = datetime.now(timezone.utc)
    match_start = _as_utc(match.start_time)
    if match.status != "upcoming" or match_start is None or match_start <= now:
        raise HTTPException(
            status_code=409,
            detail="Market evaluation is only available before an upcoming match starts.",
        )
    prediction = get_match_prediction(match_id, db)
    if isinstance(prediction, JSONResponse) or not isinstance(
        prediction, FormulaPredictionResponse
    ):
        raise HTTPException(status_code=403, detail="Prediction is unavailable for this match.")
    try:
        evaluation = evaluate_market(prediction, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    captured_at = _as_utc(payload.captured_at) or now
    if captured_at > now or captured_at >= match_start:
        raise HTTPException(
            status_code=422,
            detail="Odds capture time must be current or past and before match start.",
        )
    for outcome in evaluation["outcomes"]:
        db.add(
            MarketOddsSnapshot(
                match_id=match_id,
                bookmaker=payload.bookmaker,
                market_type=evaluation["market_type"],
                outcome=outcome["outcome"],
                decimal_odds=outcome["decimal_odds"],
                captured_at=captured_at,
            )
        )

    paper_bet_id = None
    if evaluation["paper_test_eligible"] and evaluation["best_outcome"]:
        selected = next(
            item
            for item in evaluation["outcomes"]
            if item["outcome"] == evaluation["best_outcome"]
        )
        existing = db.scalar(
            select(PaperBet).where(
                PaperBet.match_id == match_id,
                PaperBet.market_type == evaluation["market_type"],
                PaperBet.outcome == selected["outcome"],
                PaperBet.status == "pending",
            )
        )
        if existing is None:
            existing = PaperBet(
                match_id=match_id,
                market_type=evaluation["market_type"],
                outcome=selected["outcome"],
                model_probability=selected["model_probability"],
                decimal_odds=selected["decimal_odds"],
                no_vig_probability=selected["no_vig_probability"],
                edge=selected["edge"],
                expected_value=selected["expected_value"],
                stake_units=1.0,
                status="pending",
                guard_reasons_json=[],
            )
            db.add(existing)
            db.flush()
        paper_bet_id = existing.id
    db.commit()
    return MarketEvaluationResponse(
        match_id=match_id,
        bookmaker=payload.bookmaker,
        paper_bet_id=paper_bet_id,
        warning=(
            "Paper tracking only. This is not an instruction to place a real-money bet."
        ),
        **evaluation,
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@router.get("/paper-bets", response_model=list[PaperBetRead])
def list_paper_bets(
    match_id: int | None = None,
    status: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list[PaperBetRead]:
    statement = select(PaperBet).order_by(PaperBet.created_at.desc(), PaperBet.id.desc())
    if match_id is not None:
        statement = statement.where(PaperBet.match_id == match_id)
    if status:
        statement = statement.where(PaperBet.status == status)
    rows = db.scalars(statement.limit(max(1, min(500, limit)))).all()
    return [
        PaperBetRead(
            id=row.id,
            match_id=row.match_id,
            market_type=row.market_type,
            outcome=row.outcome,
            model_probability=row.model_probability,
            decimal_odds=row.decimal_odds,
            no_vig_probability=row.no_vig_probability,
            edge=row.edge,
            expected_value=row.expected_value,
            stake_units=row.stake_units,
            status=row.status,
            profit_units=row.profit_units,
            guard_reasons_json=row.guard_reasons_json,
            created_at=row.created_at,
            settled_at=row.settled_at,
        )
        for row in rows
    ]


@router.get("/paper-bets/summary")
def paper_bets_summary(db: Session = Depends(get_db)) -> dict:
    return build_paper_bet_summary(db)
