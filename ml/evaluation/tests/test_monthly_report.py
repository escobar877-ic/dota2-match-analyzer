import unittest
from datetime import datetime, timezone

from ml.evaluation.monthly_report import build_monthly_report


class MonthlyReportTests(unittest.TestCase):
    def test_monthly_report_groups_by_month(self):
        records = [
            {"start_time": datetime(2026, 1, 1, tzinfo=timezone.utc), "label": 1, "formula": 0.7, "elo": 0.6, "ml": 0.8},
            {"start_time": datetime(2026, 1, 2, tzinfo=timezone.utc), "label": 0, "formula": 0.4, "elo": 0.5, "ml": 0.3},
            {"start_time": datetime(2026, 2, 1, tzinfo=timezone.utc), "label": 1, "formula": 0.6, "elo": 0.6, "ml": None},
        ]
        report = build_monthly_report(records)
        self.assertEqual(report["2026-01"]["matches_count"], 2)
        self.assertEqual(report["2026-02"]["matches_count"], 1)
        self.assertIsNone(report["2026-02"]["ml"]["accuracy"])


if __name__ == "__main__":
    unittest.main()
