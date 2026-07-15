from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

backend_dir = Path(__file__).resolve().parents[2] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from ml.config import ML_ARTIFACT_DIR
from worker.data_ingestion.source_capabilities import get_source_capabilities
from worker.data_ingestion.sources import get_source_clients


SOURCE_HEALTH_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "source_health_report.json"


def build_source_health_report(*, artifact_path: str | Path | None = SOURCE_HEALTH_REPORT_PATH) -> dict[str, Any]:
    sources = {}
    warnings = []
    for client in get_source_clients():
        status = client.get_status()
        can_connect = None
        last_error = status.get("missing_key_reason")
        if not status["enabled"]:
            can_connect = False
        elif client.source_name == "opendota":
            health = client.fetch_matches()
            can_connect = health.ok
            last_error = health.error
            if health.error:
                warnings.append(f"{client.source_name}: {health.error}")
        elif hasattr(client, "health_check"):
            health = client.health_check()
            can_connect = health.ok
            last_error = health.error
            if health.error:
                warnings.append(f"{client.source_name}: {health.error}")
        else:
            warnings.append(f"{client.source_name}: connection check skipped unless API key is configured.")
        sources[client.source_name] = {
            **status,
            "can_connect": can_connect,
            "last_error": last_error,
            "rate_limit_warning": "Rate limits may apply; use dry-run first." if status["enabled"] else None,
        }
    report = {
        "status": "warning" if warnings else "ok",
        "generated_at": datetime.now(UTC).isoformat(),
        "sources": sources,
        "capabilities": get_source_capabilities(),
        "warnings": warnings,
    }
    if artifact_path is not None:
        path = Path(artifact_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.tmp")
        temp_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
        temp_path.replace(path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Check source connector health.")
    parser.parse_args()
    print(json.dumps(build_source_health_report(), indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
