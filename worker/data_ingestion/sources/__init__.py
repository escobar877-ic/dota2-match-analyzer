from worker.data_ingestion.sources.opendota_client import OpenDotaSourceClient
from worker.data_ingestion.sources.pandascore_client import PandaScoreSourceClient
from worker.data_ingestion.sources.stratz_client import StratzSourceClient


def get_source_clients():
    return [OpenDotaSourceClient(), StratzSourceClient(), PandaScoreSourceClient()]


def get_source_client(source: str):
    for client in get_source_clients():
        if client.source_name == source:
            return client
    raise ValueError(f"Unsupported source: {source}")
