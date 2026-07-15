from __future__ import annotations

import sys
import unittest
from pathlib import Path

repo_root = Path(__file__).resolve().parents[3]
backend_dir = repo_root / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from worker.data_ingestion.normalizer import (
    normalize_match_format,
    normalize_match_status,
    normalize_team_name,
    normalize_tournament_name,
)


class NormalizerTests(unittest.TestCase):
    def test_known_alias_matches_tier1(self):
        self.assertEqual(normalize_team_name(" liquid "), "Team Liquid")
        self.assertEqual(normalize_tournament_name("TI"), "The International")

    def test_normalize_match_format_handles_variants(self):
        self.assertEqual(normalize_match_format("bo3"), "BO3")
        self.assertEqual(normalize_match_format("BO3"), "BO3")
        self.assertEqual(normalize_match_format("Best of 3"), "BO3")

    def test_normalize_status_handles_variants(self):
        self.assertEqual(normalize_match_status("not_started"), "upcoming")
        self.assertEqual(normalize_match_status("running"), "live")
        self.assertEqual(normalize_match_status("completed"), "finished")


if __name__ == "__main__":
    unittest.main()
