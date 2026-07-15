from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import SessionLocal
from app.db.models import Match, PaperBet
from ml.config import ML_ARTIFACT_DIR


PAPER_BET_SETTLEMENT_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "paper_bet_settlement_report.json"


def settle_pending_paper_bets(
    db: Session,
    *,
    dry_run: bool = False,
    artifact_path: str | Path | None = PAPER_BET_SETTLEMENT_REPORT_PATH,
) -> dict[str, Any]:
    pending_bets = list(
        db.scalars(
            select(PaperBet)
            .options(selectinload(PaperBet.match))
            .where(PaperBet.status == "pending")
            .order_by(PaperBet.created_at.asc(), PaperBet.id.asc())
        ).all()
    )
    errors: list[str] = []
    samples: list[dict[str, Any]] = []
    settled_now = 0
    voided_now = 0
    skipped_unfinished = 0

    for bet in pending_bets:
        match = bet.match
        if match is None:
            errors.append(f"paper_bet_id={bet.id}: match missing")
            continue
        if match.status != "finished":
            skipped_unfinished += 1
            continue
        actual = actual_match_outcome(match)
        if actual is None:
            errors.append(f"paper_bet_id={bet.id}: finished match has no settleable outcome")
            continue

        status, profit = settle_one_bet(bet, actual)
        if status == "void":
            voided_now += 1
        else:
            settled_now += 1
        if len(samples) < 20:
            samples.append(
                {
                    "paper_bet_id": bet.id,
                    "match_id": bet.match_id,
                    "market_type": bet.market_type,
                    "outcome": bet.outcome,
                    "actual_outcome": actual,
                    "status": status,
                    "profit_units": profit,
                }
            )
        if not dry_run:
            bet.status = status
            bet.profit_units = profit
            bet.settled_at = datetime.now(timezone.utc)

    if dry_run:
        db.rollback()
    else:
        db.commit()

    summary = build_paper_bet_summary(db)
    report = {
        "status": "warning" if errors else "ok",
        "mode": "dry_run" if dry_run else "apply",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pending_seen": len(pending_bets),
        "settled_now": settled_now,
        "voided_now": voided_now,
        "skipped_unfinished": skipped_unfinished,
        "errors": errors,
        "samples": samples,
        "summary": summary,
        "real_bets_placed": False,
        "training_changed": False,
        "promotion_changed": False,
    }
    if artifact_path is not None:
        _write_report(report, Path(artifact_path))
    return report


def settle_paper_bets(*, dry_run: bool = False) -> dict[str, Any]:
    db = SessionLocal()
    try:
        return settle_pending_paper_bets(db, dry_run=dry_run)
    finally:
        db.close()


def build_paper_bet_summary(db: Session) -> dict[str, Any]:
    bets = list(db.scalars(select(PaperBet)).all())
    by_status = Counter(bet.status for bet in bets)
    settled = [bet for bet in bets if bet.status in {"won", "lost", "void"}]
    graded = [bet for bet in bets if bet.status in {"won", "lost"}]
    total_profit = round(sum(float(bet.profit_units or 0.0) for bet in settled), 4)
    total_staked = round(sum(float(bet.stake_units or 0.0) for bet in graded), 4)
    hit_rate = (
        round(sum(1 for bet in graded if bet.status == "won") / len(graded), 4)
        if graded
        else None
    )
    roi = round(total_profit / total_staked, 4) if total_staked > 0 else None
    return {
        "total_bets": len(bets),
        "pending_bets": by_status.get("pending", 0),
        "settled_bets": len(settled),
        "won_bets": by_status.get("won", 0),
        "lost_bets": by_status.get("lost", 0),
        "void_bets": by_status.get("void", 0),
        "total_profit_units": total_profit,
        "total_staked_units": total_staked,
        "hit_rate": hit_rate,
        "roi": roi,
        "by_status": dict(sorted(by_status.items())),
        "real_bets_placed": False,
    }


def actual_match_outcome(match: Match) -> str | None:
    if match.is_draw:
        return "draw"
    if match.winner_team_id == match.team_a_id:
        return "team_a"
    if match.winner_team_id == match.team_b_id:
        return "team_b"
    return None


def settle_one_bet(bet: PaperBet, actual_outcome: str) -> tuple[str, float]:
    if actual_outcome == "draw" and bet.market_type == "map_winner":
        return "void", 0.0
    stake = float(bet.stake_units or 1.0)
    if bet.outcome == actual_outcome:
        return "won", round(stake * (float(bet.decimal_odds) - 1.0), 4)
    return "lost", round(-stake, 4)


def _write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Settle local paper bets after match results are known.")
    parser.add_argument("--dry-run", action="store_true", help="Preview settlement without updating paper bets.")
    args = parser.parse_args()
    print(json.dumps(settle_paper_bets(dry_run=args.dry_run), indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
