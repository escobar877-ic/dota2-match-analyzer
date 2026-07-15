from __future__ import annotations

import unittest
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[3]
backend_dir = repo_root / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from app.db.models import Match, Team
from worker.data_ingestion.sync_tracked_results import classify_pandascore_result


class TrackedResultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.team_a = Team(id=1, name="Team A", external_id="100")
        self.team_b = Team(id=2, name="Team B", external_id="200")
        self.match = Match(
            id=10,
            team_a_id=1,
            team_b_id=2,
            team_a=self.team_a,
            team_b=self.team_b,
            status="upcoming",
        )

    def test_draw_is_valid_finished_result(self):
        result = classify_pandascore_result(
            self.match,
            {"status": "finished", "draw": True, "winner_id": None},
        )

        self.assertTrue(result["is_draw"])
        self.assertIsNone(result["winner_team_id"])
        self.assertIsNone(result["error"])

    def test_maps_winner_external_id_to_local_team(self):
        result = classify_pandascore_result(
            self.match,
            {"status": "finished", "draw": False, "winner_id": 200},
        )

        self.assertFalse(result["is_draw"])
        self.assertEqual(result["winner_team_id"], 2)


if __name__ == "__main__":
    unittest.main()
