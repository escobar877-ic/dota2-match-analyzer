# Worker

Data ingestion and scheduled job entrypoint.

Run sync commands inside Docker Compose:

```bash
docker-compose exec worker python -m worker.data_ingestion.sync_all
```

External API keys are optional. Clients without required keys are disabled or skipped.
