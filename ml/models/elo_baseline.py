from __future__ import annotations

import math


class EloBaselineModel:
    model_name = "elo_baseline"

    def __init__(self, elo_index: int = 0) -> None:
        self.elo_index = elo_index

    def fit(self, x, y):
        return self

    def predict_proba(self, x):
        probabilities = []
        elo_index = getattr(self, "elo_index", 0)
        for row in x:
            elo_diff = float(row[elo_index]) if len(row) > elo_index else 0.0
            team_a_probability = 1 / (1 + math.exp(-elo_diff / 400))
            probabilities.append([1 - team_a_probability, team_a_probability])
        return probabilities
