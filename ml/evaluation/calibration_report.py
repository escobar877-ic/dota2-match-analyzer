from __future__ import annotations


def build_calibration_report(y_true: list[int], probabilities: list[float], bins_count: int = 10) -> dict:
    if not y_true:
        return {"bins": [], "calibration_error": None, "warning": "No predictions available."}

    bins = []
    total_error = 0.0
    total_count = 0
    for index in range(bins_count):
        lower = index / bins_count
        upper = (index + 1) / bins_count
        selected = [
            (actual, probability)
            for actual, probability in zip(y_true, probabilities)
            if lower <= probability < upper or (index == bins_count - 1 and probability == 1.0)
        ]
        if not selected:
            bins.append(
                {
                    "bin": f"{lower:.1f}-{upper:.1f}",
                    "matches_count": 0,
                    "expected_confidence": None,
                    "actual_winrate": None,
                }
            )
            continue
        actuals = [item[0] for item in selected]
        probs = [item[1] for item in selected]
        expected = sum(probs) / len(probs)
        actual = sum(actuals) / len(actuals)
        total_error += abs(expected - actual) * len(selected)
        total_count += len(selected)
        bins.append(
            {
                "bin": f"{lower:.1f}-{upper:.1f}",
                "matches_count": len(selected),
                "expected_confidence": round(expected, 4),
                "actual_winrate": round(actual, 4),
            }
        )

    report = {
        "bins": bins,
        "calibration_error": round(total_error / total_count, 4) if total_count else None,
    }
    if len(y_true) < bins_count:
        report["warning"] = "Partial calibration report: limited data."
    return report
