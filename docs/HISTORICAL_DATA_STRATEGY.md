# Historical Tier 1 Data Strategy

Real ML quality depends on real Tier 1 historical matches. Synthetic `dev_seed` data is useful for testing the pipeline, but it is generated locally and must not be treated as real accuracy.

## Required Data

For useful pre-match training, collect Tier 1-only records with:

- team names and stable external IDs;
- tournament name and tier;
- match or map start time;
- match format;
- final winner for finished matches;
- patch context;
- roster context when available;
- source metadata and sync logs.

Minimum viable dataset:

- 300-500 Tier 1 maps or matches.

Better dataset:

- 1000+ Tier 1 maps or matches across multiple tournaments, rosters, and patches.

## Why Tier 1 Only Matters

Lower-tier data changes the distribution of team strength, match preparation, roster stability, and tournament incentives. Mixing lower-tier records into training can make predictions worse for Tier 1 matches. Tier 1 allowlists and cleanup markers should stay strict.

## Why Roster and Patch Context Matters

Roster changes can make older team results less relevant. Patch changes can shift hero strength, game pace, and strategy. Roster-aware and patch-aware features help the model understand when history is less comparable.

## Source Priority

Recommended priority:

1. PandaScore for schedule and tournament metadata, if a key exists.
2. STRATZ for detailed match stats, if a key exists.
3. OpenDota for available public match data.
4. Manual CSV import as a fallback.

Do not use scraping that violates website or API terms. API keys are optional for local startup; missing keys should not break the app.

## Source Connector Safety

Use source health and fetch planning before historical sync:

```bash
bash scripts/source_health.sh
bash scripts/historical_fetch_plan.sh
bash scripts/sync_historical_matches.sh --source opendota --start-date 2024-01-01 --end-date 2024-01-31 --limit 20
```

Historical sync defaults to dry-run. It writes no training artifacts, does not promote models, does not hard-delete, and keeps Tier 1 filtering strict. STRATZ and PandaScore remain disabled until their API keys are configured.

## Manual CSV Fallback

Use CSV import when API coverage is incomplete or when manually curated Tier 1 historical records are available:

```bash
docker compose run --rm worker python -m worker.data_ingestion.import_quality_report imports/tier1_matches_template.csv
docker compose run --rm worker python -m worker.data_ingestion.csv_import imports/tier1_matches_sample.csv --dry-run
docker compose run --rm worker python -m worker.data_ingestion.csv_import imports/tier1_matches_sample.csv --apply
```

Dry-run writes nothing. Apply mode writes only valid Tier 1 matches and records a `data_sync_logs` row with `source="csv_import"`.

Prefer `imports/tier1_matches_template.csv` for real batches. Include `source_url` when possible so reviewers can trace every row. Scores must agree with `winner_team_name`, and `series_id` + `game_number` should be stable across repeated imports.

Place real CSV batches under `imports/real_batches/`. The first batch should be validated and dry-run before apply:

```bash
bash scripts/validate_real_batch.sh imports/real_batches/tier1_2024_batch_001.csv
bash scripts/real_batch_pipeline.sh imports/real_batches/tier1_2024_batch_001.csv
```

Apply mode recalculates ratings, builds features, trains a candidate model, and runs backtest, but never promotes automatically. Fewer than 300 real Tier 1 finished matches is still insufficient for trustworthy real accuracy.

## Coverage Report

Use the coverage report to decide whether there is enough real data to train:

```bash
docker compose run --rm worker python -m worker.data_ingestion.data_coverage
bash scripts/data_coverage.sh
```

Readiness levels:

- `insufficient`: fewer than 300 Tier 1 historical matches.
- `usable`: at least 300 Tier 1 historical matches.
- `good`: at least 1000 Tier 1 historical matches.

The report is saved to:

```text
ml/artifacts/data_coverage_report.json
```

After every real import, run:

```bash
bash scripts/real_ingestion_plan.sh
bash scripts/check_import_quality.sh imports/tier1_matches_template.csv
docker compose run --rm worker python -m worker.data_ingestion.match_validation
docker compose run --rm worker python -m worker.data_ingestion.project_audit
docker compose run --rm worker python -m worker.data_ingestion.data_coverage
```

## Source Mapping Review

Real API sources use their own team IDs, league IDs, and sometimes inconsistent display names. The project keeps strict Tier 1 filtering by requiring source records to match the allowlist or a manually verified mapping in `config/source_mappings.json`.

Use:

```bash
docker compose run --rm worker python -m worker.data_ingestion.sync_review ml/artifacts/historical_sync_report.json
```

The review report lists top exclusion reasons, unknown teams, unknown tournaments, and possible alias suggestions. Suggestions are never automatically applied. Verified mappings can unlock valid rows while preserving the Tier 1-only dataset.

## STRATZ Match IDs

Use STRATZ as a details source for known match IDs, not as a date-range discovery feed. Match IDs should come from a trusted schedule source, manual research, or a curated CSV batch.

Workflow:

```bash
bash scripts/validate_stratz_match_ids.sh imports/stratz_batches/tier1_stratz_batch_001.csv
bash scripts/import_stratz_match_ids.sh imports/stratz_batches/tier1_stratz_batch_001.csv
```

Apply requires:

```bash
bash scripts/import_stratz_match_ids.sh imports/stratz_batches/tier1_stratz_batch_001.csv --apply
```

The validator cross-checks returned STRATZ teams, tournament, start date, finished status, winner, source URL, and Tier 1 allowlists. Draft data is imported only when STRATZ returns it. Draft-aware models remain experimental and are not connected to the main prediction endpoint.
