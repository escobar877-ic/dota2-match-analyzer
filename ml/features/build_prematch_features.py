from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[2] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

if "WORKER_DATABASE_URL" in os.environ:
    os.environ["DATABASE_URL"] = os.environ["WORKER_DATABASE_URL"]
elif not Path("/.dockerenv").exists():
    current_url = os.getenv("DATABASE_URL")
    if current_url and "@postgres:" in current_url:
        os.environ["DATABASE_URL"] = current_url.replace("@postgres:", "@localhost:")
    elif current_url is None:
        os.environ["DATABASE_URL"] = "postgresql+psycopg://postgres:postgres@localhost:5432/dota_analyzer"

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import selectinload

from app.database import SessionLocal
from app.db.models import Match, MatchPrematchFeature
from ml.features.feature_schema import FEATURE_VERSION, SAFE_DEFAULTS, assert_complete_feature_set
from ml.features.head_to_head_features import build_head_to_head_features
from ml.features.leakage_guard import validate_prematch_inputs
from ml.features.rating_features import build_rating_features, get_team_historical_matches
from ml.features.recent_form_features import build_recent_form_features
from ml.features.roster_patch_features import build_roster_patch_features
from ml.features.team_features import build_team_features
from ml.features.tournament_features import build_tournament_features
from worker.data_ingestion.pro_match_quality import is_verified_pro_tournament


def build_features_for_match(
    db,
    match: Match,
    *,
    allow_verified_pro_inference: bool = False,
) -> dict:
    if not is_tier1_feature_match(
        match,
        allow_verified_pro_inference=allow_verified_pro_inference,
    ):
        raise ValueError("Cannot build prematch features for non-Tier 1 match")

    historical_team_a = get_team_historical_matches(db, match.team_a_id, match.start_time)
    historical_team_b = get_team_historical_matches(db, match.team_b_id, match.start_time)
    validate_prematch_inputs(match, historical_team_a + historical_team_b)

    features = dict(SAFE_DEFAULTS)
    features.update(build_rating_features(db, match))
    features.update(build_recent_form_features(db, match))
    features.update(build_team_features(db, match))
    features.update(build_head_to_head_features(db, match))
    features.update(build_tournament_features(match, db))
    features.update(build_roster_patch_features(db, match))
    assert_complete_feature_set(features)
    return features


def build_and_store_prematch_features() -> dict[str, int]:
    db = SessionLocal()
    generated_at = datetime.now(timezone.utc)
    created = 0
    updated = 0
    skipped = 0

    try:
        matches = list(
            db.scalars(
                select(Match)
                .options(selectinload(Match.team_a), selectinload(Match.team_b))
                .where(
                    Match.status.in_(["finished", "upcoming", "live"]),
                    or_(
                        Match.is_tier1_match.is_(True),
                        and_(
                            Match.competition_tier == "pro",
                            Match.verification_status == "verified",
                            Match.is_training_eligible.is_(True),
                        ),
                    ),
                )
                .order_by(Match.start_time.asc().nullsfirst(), Match.id.asc())
            )
        )

        for match in matches:
            if not is_tier1_feature_match(match):
                skipped += 1
                print(f"Skipping match {match.id}: not Tier 1 feature eligible")
                continue
            if match.start_time is None:
                skipped += 1
                print(f"Skipping match {match.id}: missing start_time")
                continue

            features = build_features_for_match(db, match)
            existing = db.scalar(
                select(MatchPrematchFeature).where(
                    MatchPrematchFeature.match_id == match.id,
                    MatchPrematchFeature.feature_version == FEATURE_VERSION,
                )
            )
            if existing:
                existing.team_a_id = match.team_a_id
                existing.team_b_id = match.team_b_id
                existing.generated_at = generated_at
                existing.features_json = features
                updated += 1
            else:
                db.add(
                    MatchPrematchFeature(
                        match_id=match.id,
                        team_a_id=match.team_a_id,
                        team_b_id=match.team_b_id,
                        feature_version=FEATURE_VERSION,
                        generated_at=generated_at,
                        features_json=features,
                    )
                )
                created += 1

        db.commit()
        print(
            "Prematch features built: "
            f"created={created}, updated={updated}, skipped={skipped}, feature_version={FEATURE_VERSION}"
        )
        return {"created": created, "updated": updated, "skipped": skipped}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def is_tier1_feature_match(
    match: Match,
    *,
    allow_verified_pro_inference: bool = False,
) -> bool:
    verified_pro_inference = bool(
        allow_verified_pro_inference
        and match.status in {"upcoming", "live", "finished"}
        and match.competition_tier == "pro"
        and match.verification_status == "verified"
        and match.source_confidence in {"high", "medium"}
        and match.external_source not in {"dev_seed", "demo"}
    )
    return bool(
        (
            match.is_tier1_match
            or (
                match.competition_tier == "pro"
                and match.verification_status == "verified"
                and (
                    match.is_training_eligible is True
                    or verified_pro_inference
                )
            )
        )
        and match.team_a is not None
        and match.team_b is not None
        and match.tournament_name
        and is_verified_pro_tournament(match.tournament_name)
    )


if __name__ == "__main__":
    build_and_store_prematch_features()
