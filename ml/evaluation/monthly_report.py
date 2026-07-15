from __future__ import annotations

from collections import defaultdict

from ml.evaluation.metrics import calculate_classification_metrics


def build_monthly_report(records: list[dict]) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        month = record["start_time"].strftime("%Y-%m")
        grouped[month].append(record)

    report = {}
    for month, items in sorted(grouped.items()):
        month_report = {"matches_count": len(items)}
        for model_name in ["formula", "elo", "ml"]:
            probabilities = [item[model_name] for item in items if item.get(model_name) is not None]
            labels = [item["label"] for item in items if item.get(model_name) is not None]
            month_report[model_name] = calculate_classification_metrics(labels, probabilities)
        report[month] = month_report
    return report
