# Operational Next Steps

The main local product flow is implemented. Remaining work is evidence collection and data maintenance, not another feature-level rewrite.

## Automatic

The `forecast-scheduler` container runs every 15 minutes and:

1. refreshes PandaScore/EWC schedule data;
2. refreshes known upcoming rosters;
3. records immutable early/day-before/final forecasts;
4. updates tracked finished results;
5. settles prospective forecasts and paper tests;
6. writes scheduler and forecast-health reports.

Keep Docker running so final-horizon snapshots are not missed.

## Daily Check

```bash
bash scripts/system_check.sh
```

Review `/upcoming`, `/models`, and `/data`. Do not loosen Tier 1 filtering merely to increase the number of predictions.

## Data Maintenance

- Continue importing verified real match IDs/CSV batches through dry-run and validation workflows.
- Refresh match details and roster history after adding real matches.
- Keep `config/dota_patches.json`, Tier 1 allowlists, and verified source mappings reviewed manually.
- Never auto-map academy/youth teams to main rosters.

## Model Maintenance

Train candidates only after material new real data arrives. Do not promote automatically unless real coverage, artifact, backtest, calibration, and walk-forward gates pass.

```bash
bash scripts/retrain_candidate.sh
docker compose run --rm worker python -m ml.training.model_promotion --list
```

## Prospective Review Gates

- fewer than 100 settled final forecasts: collection only;
- 100-299 settled final forecasts: preliminary review;
- 300 or more settled final forecasts: serious calibration/weight review;
- preview forecasts remain separate from strict Tier 1 metrics at every sample size.

No software change can replace waiting for genuinely prospective outcomes.
