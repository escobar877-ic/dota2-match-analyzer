# Real Data Setup

This project can run fully locally without real API keys. Missing keys must not break the backend, frontend, worker, or local dev seed workflows.

## Supported Sources

- OpenDota
- STRATZ
- PandaScore

OpenDota may work partly without a key, depending on endpoint and rate limits. STRATZ and PandaScore require API keys for real sync.

## Environment Variables

Set these in `.env` when available:

```bash
OPENDOTA_API_KEY=
STRATZ_API_KEY=
PANDASCORE_API_KEY=
```

Optional safety flags:

```bash
USE_DEMO_DATA=false
USE_DEV_SEED=false
DATA_SYNC_DRY_RUN_DEFAULT=true
DATA_SYNC_REQUIRE_TIER1=true
```

Do not commit real API keys.

## Check Source Status

Open the local dashboard:

```text
http://localhost:3000/data
```

Or check the API:

```text
GET http://localhost:8000/data-sources/status
GET http://localhost:8000/sync/logs/latest
```

The status endpoint reports whether sources are enabled, whether keys are configured, source capabilities, setup hints, and whether a source is safe to sync. It does not expose key values.

## Real Ingestion Plan

Generate a read-only plan before syncing or importing:

```bash
bash scripts/real_ingestion_plan.sh
```

The plan is written to `ml/artifacts/real_ingestion_plan.json` and shows available sources, missing keys, current real historical coverage, remaining rows to reach 300/1000, and recommended next commands.

## Source Health and Historical Fetch

Check connector status without exposing API keys:

```bash
bash scripts/source_health.sh
```

Plan safe historical fetch windows:

```bash
bash scripts/historical_fetch_plan.sh
```

Dry-run historical sync before applying anything:

```bash
bash scripts/sync_historical_matches.sh --source opendota --start-date 2024-01-01 --end-date 2024-01-31 --limit 20
```

Apply requires an explicit `--apply` flag and should only happen after reviewing source health, fetch plan, dry-run output, match validation, project audit, and coverage. Missing STRATZ or PandaScore keys disable those connectors cleanly; local mode still works.

## Dry-Run Sync

Always start with dry-run:

```bash
docker compose run --rm worker python -m worker.data_ingestion.sync_all --dry-run
```

Review:

- `records_seen`
- `would_create`
- `would_update`
- `would_exclude`
- exclusion reasons

The Tier 1 allowlist protects the dataset. Lower-tier teams, tournaments, and matches must not be used for prediction or training.

## Apply Real Sync

Only apply after reviewing dry-run output:

```bash
docker compose run --rm worker python -m worker.data_ingestion.sync_all
```

Then run cleanup in dry-run mode first:

```bash
docker compose run --rm backend python -m app.tier_filter.cleanup_service --dry-run
```

Apply markers only after reviewing the dry-run summary:

```bash
docker compose run --rm backend python -m app.tier_filter.cleanup_service --apply
```

Cleanup marks Tier 1 and excluded rows. It must not hard-delete real data.

## Manual CSV Quality Flow

Use `imports/tier1_matches_template.csv` for real curated Tier 1 records. Optional fields include `series_id`, `game_number`, scores, radiant/dire names, duration, VOD URL, and source URL.

For real batches, put files under `imports/real_batches/`, for example:

```text
imports/real_batches/tier1_2024_batch_001.csv
```

Check quality before dry-run/apply:

```bash
bash scripts/validate_real_batch.sh imports/real_batches/tier1_2024_batch_001.csv
bash scripts/check_import_quality.sh imports/tier1_matches_template.csv
docker compose run --rm worker python -m worker.data_ingestion.csv_import imports/tier1_matches_template.csv --dry-run
```

Only after review:

```bash
docker compose run --rm worker python -m worker.data_ingestion.csv_import imports/tier1_matches_template.csv --apply
docker compose run --rm worker python -m worker.data_ingestion.match_validation
docker compose run --rm worker python -m worker.data_ingestion.data_coverage
```

Or use the real batch pipeline. Default mode stops before apply:

```bash
bash scripts/real_batch_pipeline.sh imports/real_batches/tier1_2024_batch_001.csv
```

Apply mode requires `--apply` and still creates only a candidate model after import; it does not promote:

```bash
bash scripts/real_batch_pipeline.sh imports/real_batches/tier1_2024_batch_001.csv --apply
```

## Build Features and Models

After real Tier 1 data is synced and reviewed:

```bash
docker compose run --rm backend python -m app.ratings.recalculate_elo
docker compose run --rm worker python -m ml.features.build_prematch_features
docker compose run --rm worker python -m ml.training.train_prematch_model
docker compose run --rm worker python -m ml.evaluation.backtest
```

## Full Real Pipeline

Dry-run only by default:

```bash
bash scripts/real_data_pipeline.sh
```

Apply mode requires an explicit flag:

```bash
bash scripts/real_data_pipeline.sh --apply
```

The real pipeline does not run dev seed. Synthetic dev seed data is useful for local testing, but it must not be used for real accuracy claims.

## Sync Review and Guarded First Apply

OpenDota and other source records may be excluded even when they are real matches because the source name/ID is not yet mapped to a canonical Tier 1 team or tournament. Do not weaken filtering to fix this.

Run the review flow:

```bash
bash scripts/first_real_sync_apply.sh --source opendota --start-date 2024-01-01 --end-date 2024-01-31 --limit 20
```

Inspect:

```text
ml/artifacts/historical_sync_report.json
ml/artifacts/sync_review_report.json
```

If the review shows unknown teams or tournaments, add only verified mappings to `config/source_mappings.json`. Fuzzy alias suggestions are not applied automatically. Unknown source names must not become Tier 1 unless they already map to a configured Tier 1 allowlist entry.

Apply is guarded:

```bash
bash scripts/first_real_sync_apply.sh --source opendota --start-date 2024-01-01 --end-date 2024-01-31 --limit 100 --apply
```

If the dry-run has zero valid rows, apply stops and reports that mappings/aliases need review.

## STRATZ Match ID Workflow

STRATZ date-range historical fetch is unsupported for the current GraphQL schema. Use STRATZ for details of specific verified Dota match IDs.

Create a CSV batch using:

```text
imports/stratz_match_ids_template.csv
```

Required fields:

- `match_id`
- `expected_team_a_name`
- `expected_team_b_name`
- `expected_tournament_name`
- `expected_start_date`
- `source_url`
- `verification_note`

Validate first:

```bash
bash scripts/validate_stratz_match_ids.sh imports/stratz_batches/tier1_stratz_batch_001.csv
```

Dry-run import:

```bash
bash scripts/import_stratz_match_ids.sh imports/stratz_batches/tier1_stratz_batch_001.csv
```

Apply only after review:

```bash
bash scripts/import_stratz_match_ids.sh imports/stratz_batches/tier1_stratz_batch_001.csv --apply
```

Validation compares STRATZ details against the expected fields and Tier 1 allowlists. Missing `source_url`, mismatched teams/tournament/date, missing winner, or non-Tier 1 records block apply. The workflow does not train or promote a model.
