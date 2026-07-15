from __future__ import annotations

import argparse

from worker.data_ingestion.sync_matches import sync_matches
from worker.data_ingestion.sync_players import sync_players
from worker.data_ingestion.sync_teams import sync_teams


def sync_all(*, dry_run: bool = False) -> dict | None:
    if dry_run:
        print("Dry-run mode: validating match sync without database writes.")
        return {"matches": sync_matches(dry_run=True)}
    sync_teams()
    sync_players()
    sync_matches()
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sync_all(dry_run=args.dry_run)
