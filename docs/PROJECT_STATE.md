# Dota 2 Match Analyzer - Current State

Last verified: 2026-07-16

## Runtime

- Docker Compose runs PostgreSQL, FastAPI, Next.js production server, worker, and forecast scheduler.
- `GET /health/ready` checks the database, active model artifact, scheduler freshness, and data coverage.
- `bash scripts/system_check.sh` is the authoritative local completion check.
- `bash scripts/backup_local.sh` creates and validates a PostgreSQL custom-format backup.

## Data

- Strict Tier 1 real historical matches: 1,643.
- Verified professional historical matches outside the strict set: 910.
- Real training-eligible rows across the strict and verified-pro profiles: 2,553.
- The Tier 1 coverage report contains 1,763 historical rows in total, including
  120 explicitly labeled `dev_seed` rows. Synthetic rows remain excluded from
  real-only backtests and accuracy claims.
- Patch coverage: 100%.
- Roster coverage: approximately 78.1%.
- PandaScore schedule sync and STRATZ/OpenDota detail workflows are optional and safe without keys.
- Tier 1 allowlists, source mappings, audits, validation, duplicate checks, and dry-run/apply guards remain enforced.

## Prediction

- Main endpoint: `GET /matches/{id}/prediction`.
- Strict prediction combines Formula, Elo, and local ML.
- Active model: `prematch_20260715142238` (model version ID 23 at verification time).
- Current backtest-driven production weights: Formula 0.55, Elo 0.29, ML 0.16.
- Backtest and walk-forward gates select weights; rejected candidates do not affect production.
- Verified-pro previews may combine guarded Formula, Elo, and compatible local ML
  components when they are available. They remain low-confidence and isolated
  from strict metrics, training, promotion, and automated betting.
- Draft model remains experimental and is not used by the main prediction endpoint.
- The API reports per-map strength separately from derived BO2/BO3/BO5 series
  outcomes. Prospective settlement evaluates the series outcome, including BO2
  draws, rather than comparing map strength with a series winner.

## Quality Evidence

- Active saved-window backtest: 397 real strict Tier 1 matches from 2026-04-25
  through 2026-07-14.
- Formula is the current saved-window leader: accuracy 0.748, log loss 0.554,
  Brier 0.185. Elo records 0.710 / 0.574 / 0.194; active ML records
  0.597 / 0.666 / 0.237.
- Walk-forward validation uses five chronological folds and 1,323 strict
  evaluation rows. The guarded ensemble records log loss 0.639 and Brier 0.224,
  but the stability gate is currently blocked because aggregate ML log loss is
  materially worse than Elo.
- Random Forest and Extra Trees candidates that did not beat the baselines were
  rejected. The active model and `ml/artifacts/active/` remain unchanged.

## Ongoing Statistical Gate

The software workflow is operational, but prediction quality is not a finished scientific claim. The scheduler must collect at least 100 settled final-horizon forecasts before preliminary prospective evaluation and preferably 300 before a serious review. Historical backtests and synthetic data must not be presented as guaranteed betting profitability.

Current known product boundaries:

- no live in-game state model;
- live OpenDota context can show current hero picks and score when a match is
  discoverable, but the feed does not provide bans or original draft order;
- no automatic bookmaker odds without an optional paid provider key;
- manual decimal odds can be evaluated and recorded as paper tests only;
- no automatic real-money betting;
- incomplete roster or draft data lowers confidence instead of being invented.
