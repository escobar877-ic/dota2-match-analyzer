import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[2]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.database import SessionLocal
from app.ratings.rating_service import recalculate_elo_ratings


def main() -> None:
    db = SessionLocal()
    try:
        result = recalculate_elo_ratings(db)
        if result["processed_matches"] == 0:
            print("No Tier 1 finished matches found. Elo/Glicko ratings were cleared and no ratings were saved.")
            return
        print(
            "Elo/Glicko recalculation complete: "
            f"processed_matches={result['processed_matches']}, "
            f"ratings_saved={result['ratings_saved']}, "
            f"dataset_scope={result['dataset_scope']}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
