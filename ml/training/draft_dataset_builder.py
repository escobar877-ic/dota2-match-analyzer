from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

backend_dir = Path(__file__).resolve().parents[2] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

if "WORKER_DATABASE_URL" in os.environ:
    os.environ["DATABASE_URL"] = os.environ["WORKER_DATABASE_URL"]
elif not Path("/.dockerenv").exists():
    current_url = os.getenv("DATABASE_URL")
    if current_url and "@postgres:" in current_url:
        os.environ["DATABASE_URL"] = current_url.replace("@postgres:", "@localhost:")

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.db.models import Match, MatchPrematchFeature, Team
from ml.config import ML_MAX_TRAINING_ROWS
from ml.features.draft_feature_schema import ALL_FEATURE_FIELDS, FEATURE_VERSION, assert_complete_feature_set
from ml.features.draft_features import build_draft_features
from ml.safety import assert_tier1_training_only
from ml.training.dataset_builder import (
    DATASET_METADATA,
    DatasetRow,
    NotEnoughTrainingDataError,
    TrainingDataset,
    materialize_dataset,
    remove_forbidden_feature_columns,
)


# draft_v1 remains pinned to its original prematch schema. A future draft
# experiment must receive a new version before consuming prematch_v4 semantics.
PREMATCH_FEATURE_VERSION = "prematch_v3"


DRAFT_DATASET_METADATA = {
    **DATASET_METADATA,
    "feature_type": "draft_experimental",
    "feature_version": FEATURE_VERSION,
    "prematch_feature_version": PREMATCH_FEATURE_VERSION,
    "used_in_main_prediction": False,
}


@dataclass(frozen=True)
class DraftDatasetSummary:
    feature_version: str
    total_eligible_matches: int
    draft_matches: int
    rows_built: int
    warning: str | None = None


def build_draft_training_dataset(db: Session, *, min_rows: int = 1) -> TrainingDataset:
    rows, _summary = build_draft_dataset_rows(db)
    if len(rows) < min_rows:
        raise NotEnoughDraftTrainingDataError(
            "No draft-aware training rows available. Run draft import/dev seed first; main prematch prediction is unchanged."
        )
    return materialize_dataset(rows, DRAFT_DATASET_METADATA)


def build_draft_dataset_rows(db: Session) -> tuple[list[DatasetRow], DraftDatasetSummary]:
    assert_tier1_training_only(DRAFT_DATASET_METADATA)
    records = _eligible_records(db)
    rows: list[DatasetRow] = []
    draft_matches = 0

    for match, prematch_features in records:
        draft_features = build_draft_features(db, match)
        if not draft_features.get("draft_available"):
            continue
        draft_matches += 1
        if match.winner_team_id == match.team_a_id:
            label = 1
        elif match.winner_team_id == match.team_b_id:
            label = 0
        else:
            continue
        combined = {
            **remove_forbidden_feature_columns(prematch_features.features_json),
            **draft_features,
        }
        assert_complete_feature_set(_with_schema_defaults(combined))
        rows.append(
            DatasetRow(
                match_id=match.id,
                start_time=match.start_time,
                features={key: combined.get(key) for key in ALL_FEATURE_FIELDS},
                label=label,
            )
        )

    warning = None
    if not rows:
        warning = "No draft-aware rows built. Draft data is experimental and not used in main prediction."
    elif len(rows) < 50:
        warning = "Draft-aware sample is very small; do not treat metrics as real accuracy."
    return rows, DraftDatasetSummary(
        feature_version=FEATURE_VERSION,
        total_eligible_matches=len(records),
        draft_matches=draft_matches,
        rows_built=len(rows),
        warning=warning,
    )


class NotEnoughDraftTrainingDataError(NotEnoughTrainingDataError):
    pass


def build_draft_dataset_summary(db: Session) -> DraftDatasetSummary:
    _rows, summary = build_draft_dataset_rows(db)
    return summary


def _eligible_records(db: Session) -> list[tuple[Match, MatchPrematchFeature]]:
    TeamA = Team.__table__.alias("team_a")
    TeamB = Team.__table__.alias("team_b")
    return list(
        db.execute(
            select(Match, MatchPrematchFeature)
            .join(MatchPrematchFeature, MatchPrematchFeature.match_id == Match.id)
            .join(TeamA, Match.team_a_id == TeamA.c.id)
            .join(TeamB, Match.team_b_id == TeamB.c.id)
            .where(
                Match.is_tier1_match.is_(True),
                Match.status == "finished",
                Match.winner_team_id.is_not(None),
                Match.start_time.is_not(None),
                MatchPrematchFeature.feature_version == PREMATCH_FEATURE_VERSION,
                TeamA.c.is_active_tier1.is_(True),
                TeamB.c.is_active_tier1.is_(True),
            )
            .order_by(Match.start_time.asc(), Match.id.asc())
            .limit(ML_MAX_TRAINING_ROWS)
        ).all()
    )


def _with_schema_defaults(features: dict[str, Any]) -> dict[str, Any]:
    return {field: features.get(field) for field in ALL_FEATURE_FIELDS}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build experimental draft-aware training dataset.")
    parser.add_argument("--summary", action="store_true", help="Print dataset summary without training a model.")
    args = parser.parse_args()
    if not args.summary:
        parser.error("Only --summary is supported in Level 22 Part 1.")

    db = SessionLocal()
    try:
        summary = build_draft_dataset_summary(db)
        print(json.dumps(summary.__dict__, indent=2, sort_keys=True))
    finally:
        db.close()


if __name__ == "__main__":
    main()
