from __future__ import annotations

from app.config import normalize_database_url_for_local_dev


def test_empty_database_url_uses_localhost_default_outside_docker():
    assert (
        normalize_database_url_for_local_dev("", in_docker=False)
        == "postgresql+psycopg://postgres:postgres@localhost:5432/dota_analyzer"
    )


def test_docker_service_hostname_rewrites_to_localhost_outside_docker():
    assert (
        normalize_database_url_for_local_dev(
            "postgresql+psycopg://postgres:postgres@postgres:5432/dota_analyzer",
            in_docker=False,
        )
        == "postgresql+psycopg://postgres:postgres@localhost:5432/dota_analyzer"
    )


def test_docker_service_hostname_is_preserved_inside_docker():
    url = "postgresql+psycopg://postgres:postgres@postgres:5432/dota_analyzer"
    assert normalize_database_url_for_local_dev(url, in_docker=True) == url
