# Dota 2 Match Analyzer - Current State

Last verified: 2026-07-15

## Runtime

- Docker Compose runs PostgreSQL, FastAPI, Next.js production server, worker, and forecast scheduler.
- `GET /health/ready` checks the database, active model artifact, scheduler freshness, and data coverage.
- `bash scripts/system_check.sh` is the authoritative local completion check.
- `bash scripts/backup_local.sh` creates and validates a PostgreSQL custom-format backup.

## Data

- Strict Tier 1 real historical matches: 358.
- Verified professional historical matches: 812.
- Synthetic dev seed matches remain labeled and are excluded from real quality claims.
- Patch coverage: 100%.
- Roster coverage: approximately 78.7%.
- PandaScore schedule sync and STRATZ/OpenDota detail workflows are optional and safe without keys.
- Tier 1 allowlists, source mappings, audits, validation, duplicate checks, and dry-run/apply guards remain enforced.

## Prediction

- Main endpoint: `GET /matches/{id}/prediction`.
- Strict prediction combines Formula, Elo, and local ML.
- Active model: `prematch_20260715142238` (model version ID 23 at verification time).
- Current guarded production weights: Formula 0.30, Elo 0.20, ML 0.50.
- Backtest and walk-forward gates select weights; rejected candidates do not affect production.
- Verified-pro previews are formula-only, low-confidence, and isolated from strict metrics, training, promotion, and automated betting.
- Draft model remains experimental and is not used by the main prediction endpoint.

## Quality Evidence

- Active saved-window backtest: 83 real strict Tier 1 matches.
- ML saved-window metrics: accuracy 0.663, log loss 0.640, Brier 0.224.
- Walk-forward validation: 5 chronological folds, 226 strict evaluation rows, stability gate passed.
- A more aggressive 0.25/0.10/0.65 Formula/Elo/ML weight candidate failed the untouched validation fold; production weights were not changed.

## Ongoing Statistical Gate

The software workflow is operational, but prediction quality is not a finished scientific claim. The scheduler must collect at least 100 settled final-horizon forecasts before preliminary prospective evaluation and preferably 300 before a serious review. Historical backtests and synthetic data must not be presented as guaranteed betting profitability.

Current known product boundaries:

- no live in-game state model;
- no automatic bookmaker odds without an optional paid provider key;
- manual decimal odds can be evaluated and recorded as paper tests only;
- no automatic real-money betting;
- incomplete roster or draft data lowers confidence instead of being invented.
