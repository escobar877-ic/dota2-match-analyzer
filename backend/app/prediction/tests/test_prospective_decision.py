from __future__ import annotations

import unittest

from app.prediction.prospective_decision import build_prospective_decision


class ProspectiveDecisionTests(unittest.TestCase):
    def test_collecting_gate_blocks_training_and_promotion(self):
        report = build_prospective_decision(_prospective(42, 1.0, "formula"))

        self.assertEqual(report["decision_status"], "collecting")
        self.assertEqual(report["remaining_to_minimum"], 58)
        self.assertFalse(report["candidate_training_allowed"])
        self.assertFalse(report["promotion_allowed"])
        self.assertFalse(report["betting_claims_allowed"])

    def test_low_final_capture_keeps_gate_collecting(self):
        report = build_prospective_decision(_prospective(100, 0.94, "ensemble"))

        self.assertEqual(report["decision_status"], "collecting")
        self.assertTrue(any("95%" in reason for reason in report["reasons"]))

    def test_missing_component_rows_keeps_gate_collecting(self):
        prospective = _prospective(100, 0.98, "ensemble")
        prospective["component_metrics"]["ml"]["sample_size"] = 80

        report = build_prospective_decision(prospective)

        self.assertEqual(report["decision_status"], "collecting")
        self.assertIn("Component predictions", report["reasons"][-1])

    def test_formula_wins_prospective_review_without_auto_promotion(self):
        report = build_prospective_decision(_prospective(120, 0.98, "formula"))

        self.assertEqual(report["decision_status"], "review_required")
        self.assertEqual(report["best_by_log_loss"], "formula")
        self.assertEqual(report["best_by_brier_score"], "formula")
        self.assertEqual(
            report["recommended_action"],
            "train_new_ml_candidate_and_review_formula_weight",
        )
        self.assertTrue(report["candidate_training_allowed"])
        self.assertFalse(report["automatic_training_enabled"])
        self.assertFalse(report["promotion_allowed"])
        self.assertFalse(report["automatic_promotion_enabled"])

    def test_preview_metrics_do_not_affect_strict_decision(self):
        prospective = _prospective(110, 1.0, "ensemble")
        prospective["verified_pro_preview"] = {
            "primary_settled_forecasts": 1000,
            "component_metrics": {"ml": {"log_loss": 0.01, "brier_score": 0.01}},
        }

        report = build_prospective_decision(prospective)

        self.assertEqual(report["best_by_log_loss"], "ensemble")
        self.assertFalse(report["verified_pro_preview_used"])


def _prospective(final_rows: int, capture_rate: float, best: str) -> dict:
    base = {
        "ensemble": (0.60, 0.21),
        "formula": (0.61, 0.22),
        "elo": (0.64, 0.23),
        "ml": (0.63, 0.225),
    }
    base[best] = (0.55, 0.18)
    return {
        "primary_settled_forecasts": final_rows,
        "coverage": {"final_capture_rate": capture_rate},
        "component_metrics": {
            component: {
                "sample_size": final_rows,
                "accuracy": 0.65,
                "log_loss": metrics[0],
                "brier_score": metrics[1],
            }
            for component, metrics in base.items()
        },
    }


if __name__ == "__main__":
    unittest.main()
