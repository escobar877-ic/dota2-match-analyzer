# Dota 2 Match Analyzer

Local-first Dota 2 match analysis project.

> Research and decision-support software. Predictions are probabilistic estimates,
> not guaranteed outcomes or automated betting instructions.

The project is a runnable local analysis system:

- Frontend: Next.js + TypeScript
- Backend: FastAPI
- Database: PostgreSQL
- Runtime: Docker Compose

No cloud services are required.

## Repository contents

This repository contains the application source, migrations, tests, Docker setup,
Tier 1 configuration, public match-ID datasets, CSV templates, and operational
scripts. Local credentials, PostgreSQL data, trained model binaries, generated
reports, and validated backups are intentionally excluded from Git.

A fresh clone can run the complete local stack and create its own data and model
artifacts. Existing operator data on the original machine is not changed by Git
operations and remains available to the running Docker services.

## Project status

- Formula + Elo + guarded local ML ensemble is active.
- Real strict Tier 1 and verified-pro datasets are stored with separate quality scopes.
- PandaScore schedule refresh and immutable prospective forecast tracking run automatically.
- Backtest, calibration, walk-forward, confidence, model-promotion, audit, validation, and coverage gates are implemented.
- Verified-pro previews and draft experiments never silently enter the main strict prediction.
- No cloud ML services and no automatic real-money betting.
- See `docs/PROJECT_STATE.md` for current verified metrics and product boundaries.

## Local start

```bash
cp .env.example .env
docker compose up -d --build --wait
```

Frontend:

```text
http://localhost:3000
```

Backend:

```text
http://localhost:8000
```

Basic health check:

```text
GET http://localhost:8000/health
```

Expected response:

```json
{
  "status": "ok",
  "service": "dota-analyzer-backend"
}
```

Full readiness check:

```text
GET http://localhost:8000/health/ready
```

The readiness response checks PostgreSQL, the active model artifact, scheduler
freshness, and real data coverage without exposing API keys.

Run the complete local verification workflow:

```bash
bash scripts/system_check.sh
```

Create a validated local database and ML-artifacts backup pair:

```bash
bash scripts/backup_local.sh
bash scripts/verify_backup_pair.sh
```

The command publishes neither file unless the PostgreSQL custom dump can be
listed and the compressed artifacts archive contains the active model and its
feature schema. Candidate artifacts and operational reports are included for
rollback and audit continuity.

`verify_backup_pair.sh` is read-only: it validates the PostgreSQL catalog,
checks the compressed archive, and prints hashes for the archived active model
and feature schema without restoring or changing the running database.

The frontend image is built with `next build` and runs with `next start`; `/data`
and `/models` are dynamic so reports are never frozen at Docker image build time.

## Database

PostgreSQL runs locally inside Docker Compose. Backend applies Alembic migrations automatically on container startup.

Seed demo data only when `USE_DEMO_DATA=true`:

```bash
docker-compose exec backend python -m app.seed
```

The seed command creates:

- 8 teams;
- 20 players;
- 10 upcoming matches;
- 20 historical matches.

For a local Python environment from the repository root, the equivalent module path is:

```bash
python -m backend.app.seed
```

## Local data sync

External API keys are optional. If a source is unavailable or a key is missing, the sync command skips that source and the app keeps running with existing local data.

Recommended Docker command:

```bash
docker-compose exec worker python -m worker.data_ingestion.sync_all
```

Equivalent local Python module command:

```bash
python -m worker.data_ingestion.sync_all
```

## Baseline prediction

The first prediction engine is a non-ML formula fallback. It uses local PostgreSQL data and safe defaults when historical data is limited.

```text
GET http://localhost:8000/matches/{id}/prediction
```

The frontend shows probability bars, confidence, factor explanations, and the prediction warning on `/matches/{id}`.

## Elo ratings

Recalculate local Elo ratings from historical finished matches:

```bash
docker-compose exec backend python -m app.ratings.recalculate_elo
```

Equivalent local Python module command:

```bash
python -m backend.app.ratings.recalculate_elo
```

Rating endpoint:

```text
GET http://localhost:8000/teams/{id}/rating
```

## Prematch features

Build local pre-match feature snapshots for future ML training. This does not train a model and only reads local PostgreSQL data.

```bash
docker-compose exec worker python -m ml.features.build_prematch_features
```

Equivalent local Python module command:

```bash
python -m ml.features.build_prematch_features
```

## Tier 1 config

Tier 1 allowlists are stored as editable JSON files:

- teams allowlist: `config/tier1_teams.json`
- tournaments allowlist: `config/tier1_tournaments.json`

## Tier 1 analysis mode

The analyzer is Tier 1 only. Lower-tier data can remain in PostgreSQL for audit/debugging, but it is excluded from:

- match lists by default;
- prediction;
- Elo recalculation;
- pre-match feature generation;
- future ML training datasets.

Tier 1 status is controlled by editable allowlists:

- `config/tier1_teams.json`
- `config/tier1_tournaments.json`

Tier 1 cleanup is safe and non-destructive. It marks local lower-tier data as excluded; it does not hard delete rows.

Apply migrations:

```bash
docker compose run --rm backend alembic upgrade head
```

Preview the cleanup plan without changing the database:

```bash
docker compose run --rm backend python -m app.tier_filter.cleanup_service --dry-run
python -m backend.app.tier_filter.cleanup_service --dry-run
```

Apply Tier 1 and excluded markers:

```bash
docker compose run --rm backend python -m app.tier_filter.cleanup_service --apply
python -m backend.app.tier_filter.cleanup_service --apply
```

`--dry-run` only prints counts and exclusion reasons. `--apply` only updates marker fields such as `is_active_tier1`, `is_tier1_match`, and `excluded_reason`. Hard delete is not used.

Tier 1 status endpoints:

```text
GET http://localhost:8000/tier1/status
GET http://localhost:8000/tier1/teams
GET http://localhost:8000/tier1/tournaments
```

## Local ML constraints

Future ML must stay lightweight and local:

- ML runs locally only;
- training runs on CPU;
- cloud ML services are forbidden;
- training data comes only from local PostgreSQL;
- training uses only Tier 1 matches;
- model artifacts are stored in `ml/artifacts/`;
- PyTorch, TensorFlow, Transformers, neural networks, and hosted ML APIs are not used at this stage;
- if ML is unavailable, missing, or fails, the app uses the formula/Elo fallback.

The policy is documented in `ml/ML_CONSTRAINTS.md` and enforced by safety helpers in `ml/safety.py`.

## First local ML model

The first ML training pipeline is local-only and CPU-first. It trains only on local PostgreSQL data from Tier 1 historical matches with pre-match feature snapshots.

Run training through Docker:

```bash
docker compose run --rm worker python -m ml.training.train_prematch_model
```

The Level 9 training command:

- uses only `matches.is_tier1_match=true`;
- requires both teams to be active Tier 1 teams;
- uses `match_features_prematch`;
- trains Logistic Regression and Random Forest candidates;
- compares against an Elo baseline;
- saves artifacts in `ml/artifacts/`;
- records metadata in `model_versions`.

The `/matches/{id}/prediction` endpoint does not use ML yet. Formula and Elo remain the runtime fallback until ML is connected in a later level.

## ML prediction fallback

`GET /matches/{id}/prediction` can use the local ML model only when all local requirements are available:

- an active `model_versions` row exists;
- `ml/artifacts/prematch_model.pkl` exists;
- `ml/artifacts/feature_schema.json` exists;
- pre-match features are available or can be built safely.

If the local model or artifacts are missing, the endpoint uses the existing formula/Elo fallback. This is expected on a fresh local database because there may be too few Tier 1 historical matches to train a model.

Train locally when enough Tier 1 data exists:

```bash
docker compose run --rm worker python -m ml.training.train_prematch_model
```

Next step: Level 10B - Local Tier 1 training fixture / dev seed for testing full ML cycle.

## Dev ML cycle

The dev ML seed creates synthetic Tier 1 data only for local pipeline testing. Dev seed data is synthetic and must not be used for real accuracy claims.

Seed synthetic Tier 1 training data:

```bash
docker compose run --rm backend python -m app.dev_seed.seed_tier1_training_data
```

Run the core local ML steps manually:

```bash
docker compose run --rm backend python -m app.ratings.recalculate_elo
docker compose run --rm worker python -m ml.features.build_prematch_features
docker compose run --rm worker python -m ml.training.train_prematch_model
```

Or run the full local cycle:

```bash
bash scripts/dev_ml_cycle.sh
```

The script does not call external APIs or cloud services. It applies migrations, creates synthetic `dev_seed` Tier 1 data, applies Tier 1 cleanup markers, recalculates Elo, builds prematch features, and trains the local model.

## Backtesting and model quality

Run local backtesting:

```bash
docker compose run --rm worker python -m ml.evaluation.backtest
```

Backtesting is local-only, uses only Tier 1 historical matches, and evaluates strictly in time order. It does not use random splits. The report compares:

- formula predictor;
- Elo baseline;
- local ML model, when artifacts are available.

The report is saved to:

```text
ml/artifacts/backtest_report.json
```

If the dataset is `dev_seed`, metrics are only for pipeline testing. Dev seed metrics are synthetic and must not be used for real accuracy claims.

Shortcut script:

```bash
bash scripts/dev_ml_backtest.sh
```

Model quality endpoints:

```text
GET http://localhost:8000/models
GET http://localhost:8000/models/{id}
GET http://localhost:8000/models/{id}/backtests
GET http://localhost:8000/backtests/latest
```

Frontend page:

```text
http://localhost:3000/models
```

Next step: Level 12 - Explainability.

## Explainability roadmap

Explainability will explain ML predictions through local feature importance. SHAP is not used at this stage, and no heavy explainability dependencies are required.

Explanations must only reference features that are actually available for a prediction. They must not invent causes or imply real-world accuracy from synthetic dev seed data.

Connecting explainability to `GET /matches/{id}/prediction` is planned for Level 12 Part 2.

## Prediction explainability

ML prediction responses now include backend-generated explanations based on local feature importance. SHAP is not used yet.

Explanations must only use feature values that are present for the match and must not invent facts. Formula fallback responses keep the existing formula explanation format.

Frontend display for these ML explanations is planned for Level 12 Part 2B.

## Ensemble Prediction Engine

`GET /matches/{id}/prediction` now attempts an ensemble prediction before falling back to the earlier ML/formula flow.

The final ensemble prediction combines three local signals:

- formula predictor;
- Elo baseline;
- local ML model.

ML is not trusted blindly. Default weights are formula `0.35`, Elo `0.25`, and ML `0.40`, but the latest backtest can adjust those weights. If formula beats ML on both `log_loss` and `brier_score`, ML weight is reduced and formula weight is increased. If ML is better on both metrics, ML can receive more weight.

If ML is unavailable, the engine can still combine formula and Elo. If only formula is available, the endpoint keeps the existing formula fallback behavior. Non-Tier 1 matches are still rejected before prediction.

When components disagree strongly, the final probability is pulled toward 50/50, confidence becomes low, and the response warning is:

```text
Prediction components disagree, confidence reduced.
```

Ensemble responses include component probabilities, weights, confidence, and a structured explanation with component summary lines.

## Real data sync and data quality

Real API integrations are optional. If `OPENDOTA_API_KEY`, `STRATZ_API_KEY`, or `PANDASCORE_API_KEY` are missing, the app still runs locally with existing local/dev data. Missing or unavailable sources are reported as controlled sync failures instead of crashing the stack.

Sync runs write audit rows to `data_sync_logs`, including source, sync type, status, counts, errors, and metadata. Read-only status is available at:

```text
GET /data-sources/status
GET /sync/logs
GET /sync/logs/latest
```

The Tier 1 team and tournament allowlists protect the dataset. Unknown teams cannot become Tier 1 automatically, lower-tier tournaments are excluded, and sync does not hard-delete existing real data.

Dry-run sync validates and normalizes data without writing to the database:

```bash
docker compose run --rm worker python -m worker.data_ingestion.sync_all --dry-run
```

Apply sync:

```bash
docker compose run --rm worker python -m worker.data_ingestion.sync_all
```

## Real data setup

Local development can use synthetic dev seed data. Real prediction quality requires real Tier 1 historical matches, reviewed sync output, and Tier 1 cleanup markers.

Setup guide:

```text
docs/REAL_DATA_SETUP.md
```

The real data pipeline is dry-run only by default:

```bash
bash scripts/real_data_pipeline.sh
```

Apply mode requires an explicit flag:

```bash
bash scripts/real_data_pipeline.sh --apply
```

The real pipeline does not run dev seed and does not hard-delete real data.

Plan and validate real ingestion before applying anything:

```bash
bash scripts/real_ingestion_plan.sh
bash scripts/check_import_quality.sh imports/tier1_matches_template.csv
docker compose run --rm worker python -m worker.data_ingestion.csv_import imports/tier1_matches_template.csv --dry-run
```

`/data` shows the source capability matrix, real ingestion plan, latest import quality report, coverage, audit, and match validation. API keys remain optional; missing keys should not break local mode.

For the first real CSV batch:

```bash
bash scripts/validate_real_batch.sh imports/real_batches/tier1_2024_batch_001.csv
bash scripts/real_batch_pipeline.sh imports/real_batches/tier1_2024_batch_001.csv
```

Apply mode is explicit and still does not promote models:

```bash
bash scripts/real_batch_pipeline.sh imports/real_batches/tier1_2024_batch_001.csv --apply
```

Check API source health and plan historical fetches:

```bash
bash scripts/source_health.sh
bash scripts/historical_fetch_plan.sh
bash scripts/sync_historical_matches.sh --source opendota --start-date 2024-01-01 --end-date 2024-01-31 --limit 20
```

Historical source sync defaults to dry-run. It does not train, promote, hard-delete, or weaken Tier 1 filtering.

## Roster and patch-aware features

Prematch feature engineering now includes roster and patch context. Roster changes can reduce feature confidence because recent line-up changes make historical team performance less comparable. Patch changes also affect feature relevance because matches on old patches may not represent the current game state.

Patches are loaded from `config/dota_patches.json`:

```bash
docker compose run --rm backend python -m app.patches.patch_service --sync-config
```

After adding older verified matches, synchronize the patch timeline and
idempotently backfill missing or stale match contexts:

```bash
docker compose run --rm backend \
  python -m app.patches.patch_service --sync-config --backfill-context
```

The patch list is maintained manually from Valve's Dota patch datafeed. The current local timeline includes patch `7.41d` (released 2026-06-04); update and re-sync the config when Valve publishes a newer gameplay patch.

Synthetic dev seed creates roster and patch data for local testing. It must not be used for real accuracy claims.

Useful commands:

```bash
docker compose run --rm backend python -m app.patches.patch_service --sync-config
docker compose run --rm backend python -m app.dev_seed.seed_tier1_training_data
docker compose run --rm worker python -m ml.features.build_prematch_features
docker compose run --rm worker python -m ml.training.train_prematch_model
```

Real roster and patch data quality is important for future accuracy. Unknown roster or patch context falls back to safe defaults instead of failing prediction.

## Calibration guard and confidence hardening

Prediction confidence is guarded before ensemble responses are returned. The app should not blindly show 70-80% probabilities when confidence is weak.

If confidence is low, the final probability is pulled closer to 50/50. Confidence can also be reduced when ensemble components disagree, no recent backtest is available, calibration error is high, a team has a recent roster change, or the current patch is very new.

Guarded responses include `confidence_guard_applied`, `confidence_reasons`, and `original_probability_before_guard` when the probability was adjusted.

## Backtest-driven ensemble weights

Ensemble weights can use the latest local backtest instead of staying static. Default weights are formula `0.35`, Elo `0.25`, and ML `0.40`, but backtest quality can shift them within safety limits.

ML does not receive a high weight automatically. If formula beats ML on both `log_loss` and `brier_score`, formula weight increases and ML weight decreases. If Elo performs better than ML, Elo can receive more weight. If ML is best, it can receive more weight, but every component remains capped between `0.10` and `0.65`.

If the latest backtest uses synthetic `dev_seed` data, responses include a warning that those weights are for local testing and are not real accuracy.

## Historical Tier 1 data collection

Real model quality depends on real Tier 1 historical matches. The synthetic `dev_seed` dataset is only for pipeline testing and must not be treated as real accuracy.

For useful training, collect at least 300-500 finished Tier 1 maps or matches with winners. A stronger dataset starts around 1000+ Tier 1 records, especially when roster and patch context are available.

Manual CSV import is available as a fallback when API coverage is incomplete:

```bash
docker compose run --rm worker python -m worker.data_ingestion.import_quality_report imports/tier1_matches_template.csv
docker compose run --rm worker python -m worker.data_ingestion.csv_import imports/tier1_matches_sample.csv --dry-run
docker compose run --rm worker python -m worker.data_ingestion.csv_import imports/tier1_matches_sample.csv --apply
```

CSV import uses the normalizer, Tier 1 allowlists, data quality checks, idempotent upsert rules, and `data_sync_logs`. Unknown teams and lower-tier tournaments are excluded. The full template supports `series_id`, `game_number`, radiant/dire names, scores, duration, `vod_url`, and `source_url`.

Generate the coverage report:

```bash
bash scripts/data_coverage.sh
```

The report is written to `ml/artifacts/data_coverage_report.json` and is also exposed through `GET /data/coverage`. See `docs/HISTORICAL_DATA_STRATEGY.md` for the full collection strategy.

## Model retraining and promotion

Training now creates a `candidate` model by default. The active model does not change until a model is explicitly promoted after review.

Safe retraining workflow:

```bash
bash scripts/retrain_candidate.sh
python -m ml.training.model_promotion --list
python -m ml.training.model_promotion --promote MODEL_VERSION_ID --reason "reviewed backtest metrics"
bash scripts/promote_model.sh MODEL_VERSION_ID "reviewed backtest metrics"
```

The default training command keeps metric-based `auto` selection. A specific
lightweight candidate can be created for isolated evaluation without changing
the active model:

```bash
docker compose run --rm worker python -m ml.training.train_prematch_model \
  --training-profile tier1_plus_verified_pro \
  --feature-set differential \
  --model extra_trees
docker compose run --rm worker python -m ml.evaluation.backtest --model-version MODEL_VERSION_ID
docker compose run --rm worker python -m ml.evaluation.walk_forward \
  --training-profile tier1_plus_verified_pro \
  --feature-set differential \
  --model extra_trees
```

`extra_trees` uses the existing local scikit-learn dependency with conservative
regularization. It is candidate-only until the same holdout, calibration, and
walk-forward promotion gates pass. Running these commands never promotes it.

Before promotion, review data coverage and backtest results. A candidate should only replace the active model when artifacts exist, backtest exists, `log_loss` and `brier_score` are not worse than the active model, and calibration is acceptable.

Synthetic `dev_seed` promotion is for local testing only and requires an explicit dev flag in automated promotion flows. It is not real accuracy. Rollback is possible by promoting an older model version that still has its artifacts.

## Accuracy improvement features

`FEATURE_VERSION=prematch_v4` keeps the existing pre-match signals and joins exact canonical team identities across trusted real sources. This prevents PandaScore, OpenDota, STRATZ, and CSV rows for the same team from fragmenting Elo, Glicko, form, head-to-head, patch, and tournament history. Synthetic `dev_seed` identities remain isolated, and Tier 1 filtering is unchanged.

New rating features include a lightweight Glicko-like rating baseline with uncertainty. Elo remains available, and Glicko adds a safer signal for teams with limited match history.

Strength-of-schedule features compare recent opponent Elo, wins against strong teams, and losses against weaker teams so weak wins are not overvalued.

Recency-weighted form gives more weight to the latest matches and adds a simple momentum signal.

Tournament context features track recent performance in the same tournament and BO3/BO5 win rates. Missing context uses safe defaults instead of failing.

These features can improve local backtests, but real accuracy still depends on collecting enough real Tier 1 historical matches.

## Draft prediction foundation

Draft data can now be stored locally through `heroes`, `match_drafts`, and `draft_snapshots`. This foundation is read-only in the app and is not used by the main `/matches/{id}/prediction` endpoint yet.

For currently running matches, the forecast scheduler also writes a read-only `live_match_context_report.json` from OpenDota's public live feed. When a PandaScore series can be matched to an exact live team pair, the match page shows the current map's hero picks and Dota match ID. OpenDota live data does not expose bans or original pick order, and this context is never added to training or the main prediction.

Draft features are experimental and can be inspected separately:

```bash
docker compose run --rm backend python -m app.heroes.hero_service --sync-config
docker compose run --rm worker python -m worker.data_ingestion.sync_hero_constants --apply
docker compose run --rm worker python -m ml.features.draft_features --match-id MATCH_ID
```

Hero comfort, patch hero win rate, and draft synergy features require real Tier 1 draft history before they can support real accuracy claims. A later draft-aware model or ensemble component can be built on this foundation.

OpenDota hero constants replace placeholder labels such as `Hero 53` with the current localized hero name. The forecast scheduler refreshes these constants without changing training or model promotion.

For EWC 2026, verified finished maps can be synchronized directly from trusted OpenDota league ID `19785`:

```bash
bash scripts/sync_ewc_map_details.sh --limit 300
bash scripts/sync_ewc_map_details.sh --apply --limit 300
```

The command requires manual team/tournament mappings from `config/source_mappings.json`, rejects unmapped or non-Tier-1 records, deduplicates existing CSV/OpenDota map IDs, and enriches only missing map stats and drafts. Valid OpenDota details are also written atomically to the shared roster cache; the scheduler reuses that cache to extend leakage-safe roster history without a second API request. It never starts training or promotion.

## Draft-aware experiment foundation

`FEATURE_VERSION=draft_v1` remains an experimental schema pinned to `prematch_v3` plus draft context fields. It is not used by the main prediction endpoint or ensemble; a future draft schema version is required before using `prematch_v4` semantics.

Build a dry summary of eligible draft-aware training rows:

```bash
docker compose run --rm worker python -m ml.training.draft_dataset_builder --summary
```

Only finished Tier 1 matches with a winner and available draft data are eligible. The dataset builder ignores lower-tier matches and returns a safe error if no draft data exists. Real draft history is required before any draft-aware metrics can be treated as meaningful. Level 22 Part 2 can train a draft-aware candidate model from this foundation.

## Draft-aware model experiment

Draft-aware model training is experimental and candidate-only. It writes separate artifacts under `ml/artifacts/draft_candidates/<version>/` and does not update active prematch artifacts or the main `/matches/{id}/prediction` endpoint.

Run the full local experiment:

```bash
bash scripts/draft_experiment.sh
```

The script builds a draft dataset summary, trains a draft-aware candidate, and writes `ml/artifacts/draft_backtest_report.json`. Draft candidates stay in `status=candidate`; there is no auto-promote path for draft models.

The draft backtest compares formula, Elo, active prematch ML when available, an experimental baseline ensemble comparison, and the draft-aware candidate model. Results from `dev_seed` are synthetic and must not be treated as real accuracy. Real Tier 1 draft history is required before trusting draft-aware metrics.

## Real Sync Review and Source Mappings

First API dry-runs can exclude every OpenDota row because source team names, IDs, or tournament names do not yet map to the strict Tier 1 allowlists. This is expected and should be reviewed, not bypassed.

Run a guarded first sync flow:

```bash
bash scripts/first_real_sync_apply.sh --source opendota --start-date 2024-01-01 --end-date 2024-01-31 --limit 20
```

This runs source health, fetch planning, historical sync dry-run, and `sync_review`. It stops before apply. If valid rows are zero, apply is blocked and the next step is to add verified manual mappings in `config/source_mappings.json`.

Mappings are source-specific and manual only:

```json
{
  "opendota": {
    "teams": {
      "external_id_or_name": "Team Liquid"
    },
    "tournaments": {
      "external_id_or_name": "The International"
    }
  }
}
```

Unknown teams or tournaments are never auto-added to Tier 1. Alias suggestions in `ml/artifacts/sync_review_report.json` are review hints only. Apply requires valid mapped Tier 1 rows and an explicit `--apply` flag.

## STRATZ Match ID Import

STRATZ date-range historical fetch is intentionally not used for apply because the current GraphQL schema supports reliable details by explicit match IDs, not the old date-range query. Use manually verified STRATZ/Dota match IDs from a trusted schedule/source.

Create a batch from `imports/stratz_match_ids_template.csv` under `imports/stratz_batches/`, then validate:

```bash
bash scripts/validate_stratz_match_ids.sh imports/stratz_batches/tier1_stratz_batch_001.csv
```

Dry-run import:

```bash
bash scripts/import_stratz_match_ids.sh imports/stratz_batches/tier1_stratz_batch_001.csv
```

Apply requires explicit review and `--apply`:

```bash
bash scripts/import_stratz_match_ids.sh imports/stratz_batches/tier1_stratz_batch_001.csv --apply
```

The workflow cross-checks expected teams, tournament, start date, source URL, winner, and Tier 1 allowlists. It writes no training artifacts and does not promote models.

## Stop services

Stop containers and keep the PostgreSQL volume:

```bash
docker-compose down
```

Stop containers and delete the PostgreSQL volume:

```bash
docker-compose down -v
```

## Project structure

```text
dota-analyzer/
  frontend/
  backend/
  worker/
  ml/
  database/
  docker-compose.yml
  .env.example
  README.md
  README_ARCHITECTURE.md
```

## Current scope

Implemented now:

- minimal Next.js frontend;
- minimal FastAPI backend;
- `/health` endpoint;
- `/teams` and `/matches` endpoints;
- SQLAlchemy models and Alembic migrations;
- worker data ingestion clients and sync commands;
- baseline formula prediction endpoint;
- local Elo team ratings;
- pre-match feature engineering snapshots;
- local PostgreSQL container;
- Docker Compose local stack.

Not implemented yet:

- ML;
- draft prediction;
- live prediction;
- match ingestion;
- advanced analytics.
# Hybrid Tier 1 + verified pro training

The production prediction scope remains Tier 1. For training, a guarded hybrid
profile can add verified professional finished matches with a lower sample
weight (`0.5`). Academy, youth, qualifier, unfinished, unknown-source, and
unverified matches remain excluded.

Paste one numeric Dota/STRATZ match ID per line into a text file:

```text
8362712345
8362812346
```

Validate with a dry-run:

```bash
bash scripts/import_stratz_ids.sh imports/stratz_match_ids.txt
```

Apply only after reviewing the classifications:

```bash
bash scripts/import_stratz_ids.sh imports/stratz_match_ids.txt --apply
```

Build a hybrid candidate without promotion:

```bash
bash scripts/train_hybrid_candidate.sh
```

Model selection uses the Tier 1 validation subset when at least five Tier 1
validation rows are available. Hybrid candidates are never promoted
automatically.

### Walk-forward model stability

Before increasing the ML component weight, validate it on multiple chronological
windows:

```bash
bash scripts/walk_forward_validation.sh
```

Each fold trains only on earlier matches and evaluates the following period on
strict Tier 1 matches. Verified professional matches may support training at
reduced weight, but they are not included in the reported evaluation metrics.
The command writes `ml/artifacts/walk_forward_report.json` and never changes the
active model, production prediction, or promotion status.

Probability calibration is also guarded chronologically. A calibrator is kept
only when it improves a later validation holdout without degrading log loss or
Brier score. Otherwise the candidate retains the model's raw probabilities and
records the rejection reason in its training report.

Compare the allowed lightweight sklearn model families on identical temporal
folds:

```bash
bash scripts/model_tournament.sh
```

The report is written to `ml/artifacts/model_tournament_report.json`. An
unstable model cannot win only because it performs well on one period.

### Series outcome probabilities

The prediction endpoint retains its guarded per-map strength signal and also
derives format-aware series outcomes:

- BO2: Team A 2-0 / draw 1-1 / Team B 2-0;
- BO3 and BO5: either team wins the series.

These outcomes assume independent maps with a stable map probability. They do
not yet model draft order or between-map adaptation, so the UI labels them as
derived rather than measured betting probabilities.

### Paper market evaluation

Match detail accepts manually entered decimal odds and calculates bookmaker
margin, no-vig probabilities, model edge, and expected value. Qualifying
signals are recorded as fixed one-unit paper tests in `paper_bets`; the feature
never places a real bet and never asks for bookmaker credentials. Low
confidence, incomplete rosters, component disagreement, new patches, and weak
edge block paper-test creation.

Market evaluation is accepted only while a match is still `upcoming` and its
scheduled start is in the future. Live and finished matches are rejected by
the API and hidden by the UI so post-result odds cannot contaminate paper-test
evidence.

Paper tests are settled locally after PandaScore publishes match results:

```bash
docker compose run --rm backend python -m app.betting.paper_bet_settlement
```

The 15-minute forecast scheduler also runs this settlement step. `/models` shows
pending, won, lost, void, profit units, hit rate, and ROI for paper tests only.

Automatic multi-bookmaker collection is optional and uses the official
SportsGameOdds API rather than scraping bookmaker websites:

```bash
bash scripts/sync_market_odds.sh
bash scripts/sync_market_odds.sh --apply
```

Set `SPORTSGAMEODDS_API_KEY` in `.env` to enable it. The default command is a
dry-run. The existing PandaScore Statistics key cannot provide bookmaker odds;
PandaScore sells that feed as a separate Odds product.

Refresh expected rosters for strict Tier 1 and separately isolated actionable
verified-pro preview matches:

```bash
bash scripts/sync_upcoming_rosters.sh
bash scripts/sync_upcoming_rosters.sh --apply
```

The command uses PandaScore team details, never changes training eligibility,
and preserves roster history. Formula snapshots use the selected active roster,
not every player ever saved for a canonical team identity. Exactly five active
players is a known roster; more than five is shown as ambiguous and reduces data
quality. When the source does not provide a roster start date, the UI shows the
tracked player count instead of claiming zero stability days.

Create immutable pre-match forecasts and settle them after PandaScore publishes
the result:

```bash
bash scripts/daily_prediction_refresh.sh
```

This pipeline never trains or promotes a model. It records predictions before
match start, supports BO2 draws, and publishes prospective accuracy, log loss,
and Brier score on `/models`.

Forecasts are immutable per horizon: `early` (24-168 hours), `day_before`
(2-24 hours), and `final` (0-2 hours). A schedule move greater than 15 minutes
creates a new immutable schedule revision; the latest `final` revision is used
for primary metrics. Run the 15-minute local scheduler with:

```bash
docker compose up -d forecast-scheduler
```

The scheduler refreshes PandaScore schedule/rosters/results and forecast
snapshots. It never trains or promotes a model.

No manual action is needed for routine forecast capture while Docker and
`forecast-scheduler` remain running. Manual input is useful only for data the
official sources do not provide automatically: verified historical match IDs,
or an optional licensed odds API key for market-value evaluation. Neither is
required for the local prediction and prospective scoring lifecycle.

On macOS, install the local login watchdog once so Docker Desktop and the
required services are restored after login or an unexpected stop:

```bash
bash scripts/install_local_autostart.sh
bash scripts/ensure_local_services.sh
```

The LaunchAgent uses a helper under `~/Library/Application Support/DotaAnalyzer`
so macOS does not need to grant a background shell access to the project inside
`Documents`. It restarts the five named containers, verifies `/health/ready`,
and creates a validated PostgreSQL custom-format dump plus a compressed
`ml/artifacts` archive at most once per 24 hours. Automatic backup pairs are retained for 14 days under
`~/Library/Application Support/DotaAnalyzer/backups`.

Check whether the scheduler is missing any required forecast horizons or
settlement work:

```bash
docker compose run --rm backend python -m app.prediction.forecast_gap_report
```

The report is written to `ml/artifacts/forecast_gap_report.json` and shown on
`/models` as Forecast operations. It checks scheduler freshness, current and
historically missed final snapshots, result settlement, and schedule drift. A
missing `final` snapshot inside two hours of match start is a failed operational
check.

The `/upcoming` page defaults to actionable coverage: strict Tier 1 predictions
plus separately labeled verified-pro previews. A preview is available only for
a source-verified professional match in an allowlisted tournament with two known
teams. It uses verified pre-match history and combines only the components that
pass runtime guards. The active prematch ML model is accepted only when its
artifact schema exactly matches the known backward-compatible feature subset;
otherwise the preview fails closed to Formula/Elo. Preview confidence is always
low, it remains excluded from strict metrics and promotion, and it never changes
the strict `/matches/{id}/prediction` access rules.

The current Esports World Cup participant set is maintained manually in
`config/tier1_teams.json`. Exact verified team identities and conservative
aliases are allowed; placeholders such as `TBD` and academy/youth rosters remain
blocked. A tournament invitation never auto-adds an unknown team to Tier 1.

## Build a real OpenDota match-ID dataset

Collect up to 1,000 finished matches from configured TI, Riyadh/EWC,
DreamLeague, ESL One, PGL Wallachia, and BLAST league endpoints:

```bash
python3 scripts/build_real_match_ids_dataset.py --limit 1000 --min-matches 800
```

Collect only settled maps from the current Esports World Cup with a one-hour
post-match safety window:

```bash
python3 scripts/build_real_match_ids_dataset.py \
  --league ewc_2026 \
  --completion-grace-minutes 60 \
  --limit 300 \
  --min-matches 1 \
  --output data/real/ewc_2026_match_ids.csv
```

The grace window prevents a map still present in OpenDota's live league feed
from being treated as a settled training result.

The default output is `data/real/real_match_ids_800_1000.csv`. A failed league
is reported and skipped without losing successful results. The generated CSV is
compatible with the guarded match-ID detail importer:

```bash
bash scripts/import_stratz_ids.sh data/real/real_match_ids_800_1000.csv
```

To refresh the full trusted-league archive without the 1,000-row cap, write a
separate file and review the importer dry-run before applying it:

```bash
python3 scripts/build_real_match_ids_dataset.py \
  --limit 5000 \
  --min-matches 1000 \
  --output data/real/all_trusted_league_matches.csv
bash scripts/import_stratz_ids.sh data/real/all_trusted_league_matches.csv
bash scripts/import_stratz_ids.sh data/real/all_trusted_league_matches.csv --apply
```

The importer treats numeric Dota match IDs from CSV, OpenDota, and STRATZ as a
shared identity, so a trusted cross-source refresh updates an existing map
instead of creating a duplicate. PandaScore IDs are not assumed to share that
namespace. Academy and unknown teams remain excluded.

### Enrich verified historical matches

After verified historical match IDs are imported, fetch OpenDota match details
to add team statistics and draft snapshots. The command targets only verified
finished `historical_training` rows, is idempotent, and does not train or
promote a model.

Review a dry-run first:

```bash
bash scripts/enrich_match_details.sh --limit 50
```

Apply the reviewed batch, or limit it to one team:

```bash
bash scripts/enrich_match_details.sh --limit 50 --apply
bash scripts/enrich_match_details.sh --team "Team Spirit" --limit 20 --apply
```

Large imports can be resumed in fixed windows. OpenDota `HTTP 429` responses
use bounded backoff and are retried without duplicating completed rows:

```bash
bash scripts/enrich_match_details.sh --offset 200 --limit 200 --sleep 1.5 --apply
```

Use `--tier1-only` to spend OpenDota rate limit only on the strict production
dataset while leaving the separately labeled verified-pro training rows
untouched:

```bash
bash scripts/enrich_match_details.sh \
  --tier1-only \
  --external-source csv_import \
  --external-source opendota \
  --limit 1000 \
  --sleep 1 \
  --apply
```

When real strict Tier 1 results exist, Elo/Glicko recalculation excludes
`dev_seed` and `demo` matches. Synthetic ratings are used only in a purely
synthetic local development database.

The latest counters are stored in
`ml/artifacts/match_detail_enrichment_report.json` and shown on `/data`.

### Leakage-safe historical roster enrichment

Verified real historical matches can be used to reconstruct bounded roster
history from OpenDota match details. Review the dry-run and then apply it:

```bash
bash scripts/enrich_roster_history.sh --limit 1000
bash scripts/enrich_roster_history.sh --limit 1000 --apply
```

Limit a roster pass to one tournament window when adding a new real batch:

```bash
bash scripts/enrich_roster_history.sh \
  --tournament "Esports World Cup" \
  --start-date 2026-07-07 \
  --end-date 2026-07-20 \
  --limit 300 \
  --apply
```

Partial date windows replace only overlapping generated roster intervals. Older
history is preserved. `--cache-only --merge-only` can rebuild known segments
from the local source cache without network access or invalidating unrelated
rows. Incremental merges extend an identical five-player roster and softly
close an overlapping old interval when a changed roster is observed. Coverage
counts a match only when both teams have exactly five unique players at kickoff.

The generated roster interval starts one second after the observed match, so a
match never receives player identities learned from its own post-match payload.
Intervals expire after 45 days without another observation and are replaced by
soft-invalidated rows rather than hard deletes. This command does not train or
promote a model; rebuild features separately after reviewing the report at
`ml/artifacts/roster_history_enrichment_report.json`.

Current PandaScore rows may reference a source-specific team record while dated
roster history belongs to the same canonical team from CSV/OpenDota. Roster
lookups resolve those exact canonical identities, but never fall back from a
real source to `dev_seed`. If no current dated real roster exists, the feature
keeps its safe unknown/default value.

### Prospective quality gates and walk-forward weights

`/models` reports immutable prospective forecasts by horizon and format, final
snapshot capture, and separate Formula, Elo, ML, and Ensemble metrics. Fewer
than 100 settled final forecasts remains a collection state and must not be
used for betting-profitability claims; 300 final forecasts is the recommended
review sample.

Verified-pro previews are tracked in a separate
`verified_pro_preview` section. Their pending and settled outcomes never enter
strict Tier 1 prospective metrics, training, model promotion, or automated
betting decisions. Formula, Elo, ML, and combined preview component metrics are
reported separately so a larger prospective sample can show whether the extra
components help before any product policy is reconsidered.

Forecast identity includes an explicit evaluation scope. If a team is manually
verified as Tier 1 after an earlier preview was captured, the scheduler creates
a new immutable `strict_tier1` snapshot without rewriting or hiding the original
`verified_pro_preview` evidence.

Schedule revisions remain stored as raw evidence, while accuracy metrics use
one latest snapshot per match and actual-time horizon. Horizons are recalculated
from `generated_at` to the final recorded match start, so a moved schedule cannot
inflate the sample. Any snapshot generated after the actual start is voided and
excluded. The broad upcoming sync also preserves existing `live` and `finished`
rows instead of downgrading them from a stale schedule response.

The scheduler also writes
`ml/artifacts/prospective_decision_report.json`. The decision remains
`collecting` until at least 100 primary final forecasts exist, final-snapshot
capture is at least 95%, and Formula, Elo, ML, and Ensemble have comparable
coverage on those same matches. Once those gates pass, `/models` shows a
manual review recommendation based on prospective log loss and Brier score.
The gate never trains or promotes a model automatically and never enables
betting-accuracy claims.

`GET /matches/{id}/forecast-history` exposes only immutable snapshots captured
before the scheduled start. On finished match pages the actual result and the
captured snapshot are shown separately; a current-model recomputation is
explicitly labeled retrospective and never counted as prospective evidence.

Walk-forward validation selects candidate ensemble weights only on earlier
chronological folds and checks them on the untouched latest fold:

```bash
docker compose run --rm worker python -m ml.evaluation.walk_forward
```

Weights stay within 0.10-0.65 per component. Production uses a walk-forward
candidate only when the stability, sample-size, log-loss, and Brier gates all
pass for the current active model. Otherwise the existing backtest/default
weights remain unchanged.
