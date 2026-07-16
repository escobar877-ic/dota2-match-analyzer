import sys
import unittest
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[3]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.tier_filter.schemas import Tier1Config, Tier1TeamConfig, Tier1TournamentConfig
from app.tier_filter.tier1_matcher import Tier1Matcher


def build_test_matcher() -> Tier1Matcher:
    return Tier1Matcher(
        Tier1Config(
            teams=[
                Tier1TeamConfig(
                    name="Team Liquid",
                    aliases=["Liquid", "Team Liquid"],
                    region="WEU",
                    active=True,
                ),
                Tier1TeamConfig(
                    name="Inactive Team",
                    aliases=["Inactive"],
                    region="WEU",
                    active=False,
                ),
                Tier1TeamConfig(
                    name="Team Spirit",
                    aliases=["Spirit"],
                    region="EEU",
                    active=True,
                ),
            ],
            tournaments=[
                Tier1TournamentConfig(
                    name="The International",
                    aliases=["TI", "The International"],
                    tier=1,
                    active=True,
                )
            ],
        )
    )


class Tier1MatcherTests(unittest.TestCase):
    def test_team_alias_works(self):
        matcher = build_test_matcher()
        self.assertTrue(matcher.is_tier1_team("Liquid"))

    def test_unknown_team_returns_false(self):
        matcher = build_test_matcher()
        self.assertFalse(matcher.is_tier1_team("Random Stack"))

    def test_project_config_classifies_team_yandex_as_tier1(self):
        matcher = Tier1Matcher()
        self.assertTrue(matcher.is_tier1_team("Team Yandex"))
        self.assertTrue(matcher.is_tier1_team("Yandex"))
        self.assertTrue(matcher.is_tier1_match("Team Yandex", "Team Spirit", "Esports World Cup"))

    def test_project_config_contains_verified_ewc_2026_participants(self):
        matcher = Tier1Matcher()
        participants = {
            "1win",
            "Aurora",
            "BetBoom Team",
            "GamerLegion",
            "Inner Circle x Insanity",
            "L1ga Team",
            "LGD Gaming",
            "Level UP",
            "MOUZ",
            "Nigma Galaxy",
            "OG",
            "PARIVISION",
            "PlayTime",
            "Poor Rangers",
            "REKONIX",
            "Rune Eaters",
            "Team Falcons",
            "Team Liquid",
            "Team Nemesis",
            "Team Spirit",
            "Team Yandex",
            "Vici Gaming",
            "Virtus.pro",
            "Xtreme Gaming",
        }
        self.assertTrue(all(matcher.is_tier1_team(team) for team in participants))
        self.assertFalse(matcher.is_tier1_team("TBD"))
        self.assertFalse(matcher.is_tier1_team("Spirit Academy"))

    def test_unknown_tournament_returns_false(self):
        matcher = build_test_matcher()
        self.assertFalse(matcher.is_tier1_tournament("Small Local Cup"))

    def test_match_true_only_when_both_teams_and_tournament_are_tier1(self):
        matcher = build_test_matcher()
        self.assertTrue(matcher.is_tier1_match("Liquid", "Spirit", "TI"))
        self.assertFalse(matcher.is_tier1_match("Liquid", "Random Stack", "TI"))
        self.assertFalse(matcher.is_tier1_match("Liquid", "Spirit", "Small Local Cup"))

    def test_inactive_team_is_not_tier1(self):
        matcher = build_test_matcher()
        self.assertFalse(matcher.is_tier1_team("Inactive"))


if __name__ == "__main__":
    unittest.main()
