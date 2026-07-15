FEATURE_VERSION = "prematch_v4"

RATING_FEATURES = [
    "team_a_elo",
    "team_b_elo",
    "elo_diff",
    "team_a_glicko",
    "team_b_glicko",
    "glicko_diff",
    "team_a_rating_uncertainty",
    "team_b_rating_uncertainty",
    "rating_uncertainty_diff",
]

RECENT_FORM_FEATURES = [
    "team_a_winrate_last_5",
    "team_b_winrate_last_5",
    "team_a_winrate_last_10",
    "team_b_winrate_last_10",
    "team_a_winrate_last_20",
    "team_b_winrate_last_20",
    "form_diff_5",
    "form_diff_10",
    "form_diff_20",
    "team_a_recency_weighted_winrate",
    "team_b_recency_weighted_winrate",
    "recency_weighted_form_diff",
    "team_a_recent_momentum",
    "team_b_recent_momentum",
    "momentum_diff",
]

STRENGTH_OF_SCHEDULE_FEATURES = [
    "team_a_avg_opponent_elo_last_10",
    "team_b_avg_opponent_elo_last_10",
    "opponent_elo_diff_last_10",
    "team_a_wins_vs_strong_teams_last_20",
    "team_b_wins_vs_strong_teams_last_20",
    "strong_team_wins_diff",
    "team_a_losses_vs_weaker_teams_last_20",
    "team_b_losses_vs_weaker_teams_last_20",
    "weak_loss_diff",
]

HEAD_TO_HEAD_FEATURES = [
    "h2h_matches_count",
    "h2h_team_a_winrate",
    "h2h_recent_weighted_score",
]

TOURNAMENT_FEATURES = [
    "tournament_tier",
    "match_format",
    "is_playoff",
    "is_elimination_match",
    "team_a_tournament_recent_winrate",
    "team_b_tournament_recent_winrate",
    "tournament_recent_winrate_diff",
    "team_a_bo3_winrate",
    "team_b_bo3_winrate",
    "bo3_winrate_diff",
    "team_a_bo5_winrate",
    "team_b_bo5_winrate",
    "bo5_winrate_diff",
]

STABILITY_FEATURES = [
    "team_a_matches_count_last_30_days",
    "team_b_matches_count_last_30_days",
    "team_a_result_variance",
    "team_b_result_variance",
]

ROSTER_FEATURES = [
    "team_a_roster_stability_days",
    "team_b_roster_stability_days",
    "roster_stability_diff",
    "team_a_same_roster_matches",
    "team_b_same_roster_matches",
    "same_roster_matches_diff",
    "team_a_recent_roster_change",
    "team_b_recent_roster_change",
    "team_a_recent_standins_count",
    "team_b_recent_standins_count",
]

PATCH_FEATURES = [
    "current_patch",
    "days_since_patch",
    "is_current_patch",
    "team_a_current_patch_winrate",
    "team_b_current_patch_winrate",
    "current_patch_winrate_diff",
    "team_a_matches_current_patch",
    "team_b_matches_current_patch",
    "patch_recency_weight",
]

ALL_FEATURE_FIELDS = (
    RATING_FEATURES
    + RECENT_FORM_FEATURES
    + STRENGTH_OF_SCHEDULE_FEATURES
    + HEAD_TO_HEAD_FEATURES
    + TOURNAMENT_FEATURES
    + STABILITY_FEATURES
    + ROSTER_FEATURES
    + PATCH_FEATURES
)

SAFE_DEFAULTS = {
    "team_a_elo": None,
    "team_b_elo": None,
    "elo_diff": None,
    "team_a_glicko": None,
    "team_b_glicko": None,
    "glicko_diff": None,
    "team_a_rating_uncertainty": None,
    "team_b_rating_uncertainty": None,
    "rating_uncertainty_diff": None,
    "team_a_winrate_last_5": None,
    "team_b_winrate_last_5": None,
    "team_a_winrate_last_10": None,
    "team_b_winrate_last_10": None,
    "team_a_winrate_last_20": None,
    "team_b_winrate_last_20": None,
    "form_diff_5": None,
    "form_diff_10": None,
    "form_diff_20": None,
    "team_a_recency_weighted_winrate": None,
    "team_b_recency_weighted_winrate": None,
    "recency_weighted_form_diff": None,
    "team_a_recent_momentum": None,
    "team_b_recent_momentum": None,
    "momentum_diff": None,
    "team_a_avg_opponent_elo_last_10": None,
    "team_b_avg_opponent_elo_last_10": None,
    "opponent_elo_diff_last_10": None,
    "team_a_wins_vs_strong_teams_last_20": 0,
    "team_b_wins_vs_strong_teams_last_20": 0,
    "strong_team_wins_diff": 0,
    "team_a_losses_vs_weaker_teams_last_20": 0,
    "team_b_losses_vs_weaker_teams_last_20": 0,
    "weak_loss_diff": 0,
    "h2h_matches_count": 0,
    "h2h_team_a_winrate": None,
    "h2h_recent_weighted_score": None,
    "tournament_tier": None,
    "match_format": None,
    "is_playoff": False,
    "is_elimination_match": False,
    "team_a_tournament_recent_winrate": None,
    "team_b_tournament_recent_winrate": None,
    "tournament_recent_winrate_diff": None,
    "team_a_bo3_winrate": None,
    "team_b_bo3_winrate": None,
    "bo3_winrate_diff": None,
    "team_a_bo5_winrate": None,
    "team_b_bo5_winrate": None,
    "bo5_winrate_diff": None,
    "team_a_matches_count_last_30_days": 0,
    "team_b_matches_count_last_30_days": 0,
    "team_a_result_variance": None,
    "team_b_result_variance": None,
    "team_a_roster_stability_days": 0,
    "team_b_roster_stability_days": 0,
    "roster_stability_diff": 0,
    "team_a_same_roster_matches": 0,
    "team_b_same_roster_matches": 0,
    "same_roster_matches_diff": 0,
    "team_a_recent_roster_change": False,
    "team_b_recent_roster_change": False,
    "team_a_recent_standins_count": 0,
    "team_b_recent_standins_count": 0,
    "current_patch": None,
    "days_since_patch": None,
    "is_current_patch": False,
    "team_a_current_patch_winrate": None,
    "team_b_current_patch_winrate": None,
    "current_patch_winrate_diff": None,
    "team_a_matches_current_patch": 0,
    "team_b_matches_current_patch": 0,
    "patch_recency_weight": 1.0,
}


def assert_complete_feature_set(features: dict) -> None:
    missing = [field for field in ALL_FEATURE_FIELDS if field not in features]
    if missing:
        raise ValueError(f"Missing prematch feature fields: {', '.join(missing)}")
