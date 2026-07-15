FEATURE_TEMPLATES = {
    "elo_diff": {
        "positive": "{team_a} has a stronger Elo rating.",
        "negative": "{team_b} has a stronger Elo rating.",
        "neutral": "Elo rating advantage is unclear.",
    },
    "glicko_diff": {
        "positive": "{team_a} has a stronger uncertainty-adjusted rating.",
        "negative": "{team_b} has a stronger uncertainty-adjusted rating.",
        "neutral": "Uncertainty-adjusted rating advantage is unclear.",
    },
    "recency_weighted_form_diff": {
        "positive": "{team_a} has better recency-weighted form.",
        "negative": "{team_b} has better recency-weighted form.",
        "neutral": "Recency-weighted form is unclear.",
    },
    "opponent_elo_diff_last_10": {
        "positive": "{team_a} has faced stronger recent opponents.",
        "negative": "{team_b} has faced stronger recent opponents.",
        "neutral": "Recent strength of schedule is unclear.",
    },
    "strong_team_wins_diff": {
        "positive": "{team_a} has more recent wins against strong teams.",
        "negative": "{team_b} has more recent wins against strong teams.",
        "neutral": "Recent wins against strong teams are balanced or limited.",
    },
    "weak_loss_diff": {
        "positive": "{team_b} has more recent losses against weaker teams.",
        "negative": "{team_a} has more recent losses against weaker teams.",
        "neutral": "Recent losses against weaker teams are balanced or limited.",
    },
    "momentum_diff": {
        "positive": "{team_a} has stronger recent momentum.",
        "negative": "{team_b} has stronger recent momentum.",
        "neutral": "Recent momentum is unclear.",
    },
    "form_diff_5": {
        "positive": "{team_a} has better recent form over the last 5 matches.",
        "negative": "{team_b} has better recent form over the last 5 matches.",
        "neutral": "Recent 5-match form is unclear.",
    },
    "form_diff_10": {
        "positive": "{team_a} has better recent form over the last 10 matches.",
        "negative": "{team_b} has better recent form over the last 10 matches.",
        "neutral": "Recent 10-match form is unclear.",
    },
    "form_diff_20": {
        "positive": "{team_a} has better recent form over the last 20 matches.",
        "negative": "{team_b} has better recent form over the last 20 matches.",
        "neutral": "Recent 20-match form is unclear.",
    },
    "h2h_team_a_winrate": {
        "positive": "{team_a} has stronger head-to-head results.",
        "negative": "{team_b} has stronger head-to-head results.",
        "neutral": "Head-to-head results are limited or balanced.",
    },
    "h2h_recent_weighted_score": {
        "positive": "{team_a} has better recent head-to-head performance.",
        "negative": "{team_b} has better recent head-to-head performance.",
        "neutral": "Recent head-to-head performance is unclear.",
    },
    "team_a_winrate_last_10": {
        "positive": "{team_a}'s last-10 win rate supports the prediction.",
        "negative": "{team_a}'s last-10 win rate is a risk.",
        "neutral": "{team_a}'s last-10 win rate is unavailable or neutral.",
    },
    "team_b_winrate_last_10": {
        "positive": "{team_b}'s last-10 win rate is a risk for {team_a}.",
        "negative": "{team_b}'s last-10 win rate supports {team_a}.",
        "neutral": "{team_b}'s last-10 win rate is unavailable or neutral.",
    },
    "tournament_tier": {
        "positive": "Tournament tier context supports {team_a}.",
        "negative": "Tournament tier context is a risk for {team_a}.",
        "neutral": "Tournament tier context is unavailable or neutral.",
    },
    "match_format": {
        "positive": "Match format context supports {team_a}.",
        "negative": "Match format context is a risk for {team_a}.",
        "neutral": "Match format context is unavailable or neutral.",
    },
}


def render_factor_text(feature_name: str, impact: float, team_a_name: str, team_b_name: str) -> str:
    template = FEATURE_TEMPLATES.get(feature_name)
    if template is None:
        return f"{feature_name} affects the prediction."
    if impact > 0:
        key = "positive"
    elif impact < 0:
        key = "negative"
    else:
        key = "neutral"
    return template[key].format(team_a=team_a_name, team_b=team_b_name)
