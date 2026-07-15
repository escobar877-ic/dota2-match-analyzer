import unittest

from ml.evaluation.calibration_report import build_calibration_report


class CalibrationReportTests(unittest.TestCase):
    def test_calibration_report_returns_bins_and_error(self):
        report = build_calibration_report([0, 1, 1, 0], [0.1, 0.8, 0.7, 0.3], bins_count=5)
        self.assertEqual(len(report["bins"]), 5)
        self.assertIsNotNone(report["calibration_error"])

    def test_calibration_report_handles_empty_data(self):
        report = build_calibration_report([], [])
        self.assertIsNone(report["calibration_error"])
        self.assertIn("warning", report)


if __name__ == "__main__":
    unittest.main()
