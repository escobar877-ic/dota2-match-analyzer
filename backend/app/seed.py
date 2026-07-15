import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if "app" not in sys.modules:
    backend_dir = Path(__file__).resolve().parents[1]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

from sqlalchemy import func, select

from app.config import settings
from app.database import SessionLocal
from app.db.models import Match, Player, Team, TeamMatchStats


TEAM_NAMES = [
    ("Team Radiant", "EU", "Germany"),
    ("Dire Wolves", "CIS", "Ukraine"),
    ("Ancient Guard", "SEA", "Philippines"),
    ("Roshan Hunters", "NA", "United States"),
    ("Aegis Club", "SA", "Brazil"),
    ("Lotus Five", "China", "China"),
    ("Black King Bar", "EU", "Sweden"),
    ("Smoke Breakers", "CIS", "Kazakhstan"),
]

PLAYER_NAMES = [
    "Nova",
    "Flux",
    "Manta",
    "Echo",
    "Blink",
    "Glyph",
    "Rift",
    "Hex",
    "Pulse",
    "Vortex",
    "Fable",
    "Rune",
    "Saber",
    "Orbit",
    "Zenith",
    "Nexus",
    "Comet",
    "Apex",
    "Cipher",
    "Vector",
]


def seed_demo_data() -> None:
    if not settings.use_demo_data:
        print("USE_DEMO_DATA=false; skipping demo seed.")
        return

    db = SessionLocal()
    try:
        demo_team_count = db.scalar(
            select(func.count()).select_from(Team).where(Team.external_source == "demo")
        )
        if demo_team_count:
            print("Demo data already exists; skipping seed.")
            return

        teams = [
            Team(
                external_source="demo",
                external_id=f"demo-team-{index + 1}",
                name=name,
                logo_url=None,
                country=country,
                region=region,
            )
            for index, (name, region, country) in enumerate(TEAM_NAMES)
        ]
        db.add_all(teams)
        db.flush()

        players = []
        roles = ["carry", "mid", "offlane", "support", "hard_support"]
        for index, nickname in enumerate(PLAYER_NAMES):
            team = teams[index % len(teams)]
            players.append(
                Player(
                    external_source="demo",
                    external_id=f"demo-player-{index + 1}",
                    nickname=nickname,
                    real_name=f"{nickname} Demo",
                    team_id=team.id,
                    role=roles[index % len(roles)],
                    country=team.country,
                )
            )
        db.add_all(players)

        now = datetime.now(timezone.utc).replace(microsecond=0)
        matches = []

        for index in range(10):
            team_a = teams[index % len(teams)]
            team_b = teams[(index + 3) % len(teams)]
            matches.append(
                Match(
                    external_source="demo",
                    external_id=f"demo-upcoming-{index + 1}",
                    team_a_id=team_a.id,
                    team_b_id=team_b.id,
                    tournament_name="Demo Pro League",
                    tournament_tier="A",
                    start_time=now + timedelta(days=index + 1),
                    format="bo3",
                    status="upcoming",
                    winner_team_id=None,
                )
            )

        for index in range(20):
            team_a = teams[index % len(teams)]
            team_b = teams[(index + 2) % len(teams)]
            winner = team_a if index % 2 == 0 else team_b
            matches.append(
                Match(
                    external_source="demo",
                    external_id=f"demo-historical-{index + 1}",
                    team_a_id=team_a.id,
                    team_b_id=team_b.id,
                    tournament_name="Demo Archive Cup",
                    tournament_tier="B",
                    start_time=now - timedelta(days=index + 1),
                    format="bo3",
                    status="finished",
                    winner_team_id=winner.id,
                )
            )

        db.add_all(matches)
        db.flush()

        stats = []
        for index, match in enumerate(matches[10:]):
            team_a_won = match.winner_team_id == match.team_a_id
            duration = 1900 + (index * 37) % 900
            stats.extend(
                [
                    TeamMatchStats(
                        match_id=match.id,
                        team_id=match.team_a_id,
                        side="radiant",
                        kills=22 + index % 12,
                        deaths=18 + index % 9,
                        assists=48 + index % 20,
                        gold_diff_10=900 if team_a_won else -700,
                        xp_diff_10=600 if team_a_won else -500,
                        duration=duration,
                        result="win" if team_a_won else "loss",
                    ),
                    TeamMatchStats(
                        match_id=match.id,
                        team_id=match.team_b_id,
                        side="dire",
                        kills=18 + index % 9,
                        deaths=22 + index % 12,
                        assists=42 + index % 20,
                        gold_diff_10=-900 if team_a_won else 700,
                        xp_diff_10=-600 if team_a_won else 500,
                        duration=duration,
                        result="loss" if team_a_won else "win",
                    ),
                ]
            )
        db.add_all(stats)
        db.commit()

        print("Seeded demo data: 8 teams, 20 players, 10 upcoming matches, 20 historical matches.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_demo_data()
