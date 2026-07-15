from __future__ import annotations

import unittest

from app.ratings.team_identity import canonical_team_identity_name


class TeamIdentityTests(unittest.TestCase):
    def test_exact_normalized_names_and_configured_aliases_share_identity(self):
        self.assertEqual(canonical_team_identity_name("MOUZ"), canonical_team_identity_name("mouz"))
        self.assertEqual(canonical_team_identity_name("Team Spirit"), canonical_team_identity_name("Spirit"))

    def test_academy_and_false_fuzzy_matches_remain_separate(self):
        self.assertNotEqual(canonical_team_identity_name("Spirit Academy"), canonical_team_identity_name("Team Spirit"))
        self.assertNotEqual(canonical_team_identity_name("x5 Gaming"), canonical_team_identity_name("Xtreme Gaming"))
        self.assertNotEqual(canonical_team_identity_name("Amaru Gaming"), canonical_team_identity_name("Aurora Gaming"))


if __name__ == "__main__":
    unittest.main()
