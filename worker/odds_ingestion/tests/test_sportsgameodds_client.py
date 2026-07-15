from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from worker.odds_ingestion.sportsgameodds_client import (
    SportsGameOddsClient,
    american_or_decimal_to_decimal,
    normalize_event_odds,
)


class SportsGameOddsClientTests(unittest.TestCase):
    def test_disabled_without_key(self):
        with patch.dict(os.environ, {"SPORTSGAMEODDS_API_KEY": ""}):
            client = SportsGameOddsClient()

        self.assertFalse(client.is_enabled())
        self.assertFalse(client.fetch_upcoming_odds().ok)
        self.assertNotIn("secret", str(client.get_status()))

    def test_converts_american_odds(self):
        self.assertEqual(american_or_decimal_to_decimal("+130"), 2.3)
        self.assertEqual(american_or_decimal_to_decimal("-200"), 1.5)
        self.assertEqual(american_or_decimal_to_decimal("2.25"), 2.25)

    def test_normalizes_bookmaker_moneyline_quotes(self):
        event = {
            "eventID": "event-1",
            "teams": {
                "home": {"names": {"long": "Team Liquid"}},
                "away": {"names": {"long": "Team Spirit"}},
            },
            "status": {"startsAt": "2026-07-10T12:00:00Z"},
            "odds": {
                "home": {
                    "betTypeID": "ml",
                    "periodID": "game",
                    "sideID": "home",
                    "statEntityID": "home",
                    "byBookmaker": {
                        "pinnacle": {
                            "odds": "-125",
                            "available": True,
                            "lastUpdatedAt": "2026-07-03T10:00:00Z",
                        }
                    },
                }
            },
        }

        records = normalize_event_odds(event)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["bookmaker"], "pinnacle")
        self.assertEqual(records[0]["outcome"], "home")
        self.assertEqual(records[0]["decimal_odds"], 1.8)


if __name__ == "__main__":
    unittest.main()
