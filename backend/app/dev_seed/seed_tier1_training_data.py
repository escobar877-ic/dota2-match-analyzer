from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.database import SessionLocal
from app.db.models import DraftSnapshot, Hero, Match, MatchDraft, MatchPatchContext, MatchPrematchFeature, Player, Prediction, Team, TeamMatchStats, TeamRating, TeamRoster
from app.drafts.draft_service import build_draft_snapshot
from app.heroes.hero_service import sync_heroes_from_config
from app.patches.patch_service import sync_patches_from_config, upsert_match_patch_context
from app.tier_filter.tier1_config_loader import load_tier1_config


DEV_SOURCE = "dev_seed"
TEAM_COUNT = 12
FINISHED_MATCH_COUNT = 120
UPCOMING_MATCH_COUNT = 8


def seed_tier1_training_data() -> dict[str, int]:
    config = load_tier1_config()
    teams_config = config.teams[:TEAM_COUNT]
    tournaments_config = config.tournaments
    if len(teams_config) < TEAM_COUNT or not tournaments_config:
        raise RuntimeError("Tier 1 config does not contain enough teams/tournaments for dev seed.")

    db = SessionLocal()
    try:
        _delete_existing_dev_seed(db)

        teams = []
        for index, team_config in enumerate(teams_config):
            team = Team(
                external_source=DEV_SOURCE,
                external_id=f"dev-team-{index + 1}",
                name=team_config.name,
                country=None,
                region=team_config.region,
                tier="tier1",
                is_active_tier1=True,
                excluded_reason=None,
            )
            teams.append(team)
            db.add(team)
        db.flush()

        players = []
        roles = ["carry", "mid", "offlane", "support", "hard_support"]
        for team_index, team in enumerate(teams):
            for role_index, role in enumerate(roles):
                players.append(
                    Player(
                        external_source=DEV_SOURCE,
                        external_id=f"dev-player-{team_index + 1}-{role_index + 1}",
                        nickname=f"{team.name} Dev {role_index + 1}",
                        real_name=None,
                        team_id=team.id,
                        role=role,
                        country=None,
                    )
                )
        db.add_all(players)
        db.flush()

        now = datetime.now(timezone.utc).replace(microsecond=0)
        sync_heroes_from_config(db)
        sync_patches_from_config(db)
        rosters = []
        for team_index, team in enumerate(teams):
            team_players = players[team_index * len(roles) : (team_index + 1) * len(roles)]
            for player_index, player in enumerate(team_players):
                start_days = 140 + (team_index % 4) * 10
                if team_index % 5 == 0 and player_index == 4:
                    start_days = 18
                rosters.append(
                    TeamRoster(
                        team_id=team.id,
                        player_id=player.id,
                        role=player.role,
                        start_date=now - timedelta(days=start_days),
                        end_date=None,
                        is_active=True,
                        source=DEV_SOURCE,
                    )
                )
        db.add_all(rosters)

        strengths = {team.id: 1500 + index * 18 for index, team in enumerate(teams)}
        matches: list[Match] = []

        for index in range(FINISHED_MATCH_COUNT):
            team_a = teams[index % len(teams)]
            team_b = teams[(index * 5 + 3) % len(teams)]
            if team_a.id == team_b.id:
                team_b = teams[(index + 1) % len(teams)]
            tournament = tournaments_config[index % len(tournaments_config)]
            score_a = strengths[team_a.id] + ((index % 7) - 3) * 9
            score_b = strengths[team_b.id] + (((index + 2) % 7) - 3) * 9
            winner = team_a if (score_a >= score_b) == (index % 5 != 0) else team_b
            matches.append(
                Match(
                    external_source=DEV_SOURCE,
                    external_id=f"dev-finished-{index + 1}",
                    team_a_id=team_a.id,
                    team_b_id=team_b.id,
                    tournament_name=tournament.name,
                    tournament_tier="S",
                    start_time=now - timedelta(days=FINISHED_MATCH_COUNT - index),
                    format="BO3" if index % 9 else "BO5",
                    status="finished",
                    winner_team_id=winner.id,
                    is_tier1_match=True,
                    excluded_reason=None,
                )
            )

        for index in range(UPCOMING_MATCH_COUNT):
            team_a = teams[(index * 2) % len(teams)]
            team_b = teams[(index * 2 + 5) % len(teams)]
            tournament = tournaments_config[index % len(tournaments_config)]
            matches.append(
                Match(
                    external_source=DEV_SOURCE,
                    external_id=f"dev-upcoming-{index + 1}",
                    team_a_id=team_a.id,
                    team_b_id=team_b.id,
                    tournament_name=tournament.name,
                    tournament_tier="S",
                    start_time=now + timedelta(days=index + 1),
                    format="bo3",
                    status="upcoming",
                    winner_team_id=None,
                    is_tier1_match=True,
                    excluded_reason=None,
                )
            )

        db.add_all(matches)
        db.flush()
        for match in matches:
            upsert_match_patch_context(db, match)
        draft_entries = _build_drafts(db, matches, teams, players)
        db.add_all(draft_entries)
        db.flush()
        for match in matches:
            build_draft_snapshot(db, match.id, source=DEV_SOURCE)
        db.add_all(_build_stats(matches[:FINISHED_MATCH_COUNT]))
        db.commit()

        result = {
            "teams": len(teams),
            "players": len(players),
            "finished_matches": FINISHED_MATCH_COUNT,
            "upcoming_matches": UPCOMING_MATCH_COUNT,
            "roster_entries": len(rosters),
        }
        print(
            "Seeded synthetic Tier 1 dev data: "
            f"teams={result['teams']}, players={result['players']}, "
            f"finished_matches={result['finished_matches']}, upcoming_matches={result['upcoming_matches']}. "
            "This data is synthetic and must not be used for real accuracy claims."
        )
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _delete_existing_dev_seed(db) -> None:
    dev_match_ids = list(db.scalars(select(Match.id).where(Match.external_source == DEV_SOURCE)).all())
    dev_team_ids = list(db.scalars(select(Team.id).where(Team.external_source == DEV_SOURCE)).all())

    if dev_match_ids:
        db.execute(delete(Prediction).where(Prediction.match_id.in_(dev_match_ids)))
        db.execute(delete(DraftSnapshot).where(DraftSnapshot.match_id.in_(dev_match_ids)))
        db.execute(delete(MatchDraft).where(MatchDraft.match_id.in_(dev_match_ids)))
        db.execute(delete(MatchPatchContext).where(MatchPatchContext.match_id.in_(dev_match_ids)))
        db.execute(delete(MatchPrematchFeature).where(MatchPrematchFeature.match_id.in_(dev_match_ids)))
        db.execute(delete(TeamMatchStats).where(TeamMatchStats.match_id.in_(dev_match_ids)))
        db.execute(delete(Match).where(Match.id.in_(dev_match_ids)))
    if dev_team_ids:
        dev_player_ids = list(db.scalars(select(Player.id).where(Player.team_id.in_(dev_team_ids))).all())
        if dev_player_ids:
            db.execute(delete(TeamRoster).where(TeamRoster.player_id.in_(dev_player_ids)))
        db.execute(delete(Player).where(Player.team_id.in_(dev_team_ids)))
        db.execute(delete(TeamRating).where(TeamRating.team_id.in_(dev_team_ids)))
        db.execute(delete(Team).where(Team.id.in_(dev_team_ids)))
    db.flush()


def _build_drafts(db, matches: list[Match], teams: list[Team], players: list[Player]) -> list[MatchDraft]:
    heroes = list(db.scalars(select(Hero).where(Hero.is_active.is_(True)).order_by(Hero.hero_id.asc())).all())
    if len(heroes) < 10:
        return []
    players_by_team = {team.id: [player for player in players if player.team_id == team.id] for team in teams}
    entries: list[MatchDraft] = []
    for match_index, match in enumerate(matches):
        if match.status == "finished" and match_index % 3 == 2:
            continue
        picks_per_team = 5 if match.status == "finished" else (3 if match_index % 2 == 0 else 0)
        bans_per_team = 3 if match.status == "finished" else (1 if picks_per_team else 0)
        draft_order = 1
        for ban_index in range(bans_per_team):
            for team_id, offset, side in [(match.team_a_id, 0, "radiant"), (match.team_b_id, 5, "dire")]:
                hero = heroes[(match_index + ban_index + offset + 7) % len(heroes)]
                entries.append(
                    MatchDraft(
                        match_id=match.id,
                        team_id=team_id,
                        hero_id=hero.id,
                        action_type="ban",
                        ban_order=ban_index + 1,
                        draft_order=draft_order,
                        side=side,
                        source=DEV_SOURCE,
                    )
                )
                draft_order += 1
        for pick_index in range(picks_per_team):
            for team_id, offset, side in [(match.team_a_id, 0, "radiant"), (match.team_b_id, 5, "dire")]:
                hero = heroes[(match_index + pick_index + offset) % len(heroes)]
                team_players = players_by_team.get(team_id) or []
                player = team_players[pick_index % len(team_players)] if team_players else None
                entries.append(
                    MatchDraft(
                        match_id=match.id,
                        team_id=team_id,
                        hero_id=hero.id,
                        player_id=player.id if player else None,
                        action_type="pick",
                        pick_order=pick_index + 1,
                        draft_order=draft_order,
                        side=side,
                        source=DEV_SOURCE,
                    )
                )
                draft_order += 1
    return entries


def _build_stats(matches: list[Match]) -> list[TeamMatchStats]:
    stats = []
    for index, match in enumerate(matches):
        team_a_won = match.winner_team_id == match.team_a_id
        duration = 1800 + (index * 23) % 1100
        stats.extend(
            [
                TeamMatchStats(
                    match_id=match.id,
                    team_id=match.team_a_id,
                    side="radiant",
                    kills=28 + index % 18 if team_a_won else 18 + index % 12,
                    deaths=18 + index % 12 if team_a_won else 28 + index % 18,
                    assists=55 + index % 24 if team_a_won else 42 + index % 18,
                    gold_diff_10=900 + index * 7 if team_a_won else -750 - index * 5,
                    xp_diff_10=650 + index * 5 if team_a_won else -600 - index * 3,
                    duration=duration,
                    result="win" if team_a_won else "loss",
                ),
                TeamMatchStats(
                    match_id=match.id,
                    team_id=match.team_b_id,
                    side="dire",
                    kills=18 + index % 12 if team_a_won else 28 + index % 18,
                    deaths=28 + index % 18 if team_a_won else 18 + index % 12,
                    assists=42 + index % 18 if team_a_won else 55 + index % 24,
                    gold_diff_10=-900 - index * 7 if team_a_won else 750 + index * 5,
                    xp_diff_10=-650 - index * 5 if team_a_won else 600 + index * 3,
                    duration=duration,
                    result="loss" if team_a_won else "win",
                ),
            ]
        )
    return stats


def main() -> None:
    seed_tier1_training_data()


if __name__ == "__main__":
    main()
