# STRATZ match ID batches

Put manually verified STRATZ/Dota match ID CSV batches in this folder.

Use the same columns as `imports/stratz_match_ids_template.csv`:

- `match_id`
- `expected_team_a_name`
- `expected_team_b_name`
- `expected_tournament_name`
- `expected_start_date`
- `source_url`
- `verification_note`

Example filename:

`tier1_stratz_batch_001.csv`

Rules:

- `match_id` must be a real Dota match ID.
- `source_url` is required for trusted import review.
- `expected_*` fields are cross-checked against STRATZ details.
- Run validation before import.
- Default import mode is dry-run.
- Apply only after validation says the batch is safe.
- Do not use lower-tier, academy, qualifier, or unverified matches.

Commands:

```bash
bash scripts/validate_stratz_match_ids.sh imports/stratz_batches/tier1_stratz_batch_001.csv
bash scripts/import_stratz_match_ids.sh imports/stratz_batches/tier1_stratz_batch_001.csv
bash scripts/import_stratz_match_ids.sh imports/stratz_batches/tier1_stratz_batch_001.csv --apply
```
