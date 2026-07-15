from __future__ import annotations

from worker.data_ingestion.opendota_client import OpenDotaClient
from worker.data_ingestion.pandascore_client import PandaScoreClient
from worker.data_ingestion.stratz_client import StratzClient


def get_clients():
    return [
        OpenDotaClient(),
        StratzClient(),
        PandaScoreClient(),
    ]
