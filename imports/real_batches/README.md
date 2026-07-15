# Real Tier 1 CSV Batches

Put manually curated real Tier 1 CSV batches in this folder.

Use the same columns as:

```text
imports/tier1_matches_template.csv
```

Recommended filename format:

```text
tier1_2024_batch_001.csv
```

Always validate and dry-run first:

```bash
bash scripts/validate_real_batch.sh imports/real_batches/tier1_2024_batch_001.csv
bash scripts/real_batch_pipeline.sh imports/real_batches/tier1_2024_batch_001.csv
```

Apply only after reviewing validation, import quality, dry-run output, audit, validation, and coverage:

```bash
bash scripts/real_batch_pipeline.sh imports/real_batches/tier1_2024_batch_001.csv --apply
```

Do not put synthetic `dev_seed` data here. Do not rename synthetic data as real data.
