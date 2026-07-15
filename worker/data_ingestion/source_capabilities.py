from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SourceCapability:
    requires_api_key: bool
    supports_matches: bool
    supports_teams: bool
    supports_tournaments: bool
    supports_rosters: bool
    supports_drafts: bool
    supports_finished_results: bool
    supports_upcoming_matches: bool
    reliability_notes: str


SOURCE_CAPABILITIES: dict[str, SourceCapability] = {
    "opendota": SourceCapability(
        requires_api_key=False,
        supports_matches=True,
        supports_teams=True,
        supports_tournaments=True,
        supports_rosters=False,
        supports_drafts=True,
        supports_finished_results=True,
        supports_upcoming_matches=False,
        reliability_notes="Public match data may be incomplete and rate-limited; API key improves limits but is optional.",
    ),
    "stratz": SourceCapability(
        requires_api_key=True,
        supports_matches=True,
        supports_teams=True,
        supports_tournaments=True,
        supports_rosters=True,
        supports_drafts=True,
        supports_finished_results=True,
        supports_upcoming_matches=False,
        reliability_notes="Best candidate for detailed historical match and draft stats when a key is configured.",
    ),
    "pandascore": SourceCapability(
        requires_api_key=True,
        supports_matches=True,
        supports_teams=True,
        supports_tournaments=True,
        supports_rosters=True,
        supports_drafts=False,
        supports_finished_results=True,
        supports_upcoming_matches=True,
        reliability_notes="Useful for schedules, tournament metadata, teams, and upcoming matches.",
    ),
    "csv_import": SourceCapability(
        requires_api_key=False,
        supports_matches=True,
        supports_teams=True,
        supports_tournaments=True,
        supports_rosters=False,
        supports_drafts=False,
        supports_finished_results=True,
        supports_upcoming_matches=True,
        reliability_notes="Manual fallback. Quality depends on curated source URLs and strict Tier 1 validation.",
    ),
}


def get_source_capabilities() -> dict[str, dict]:
    return {source: asdict(capability) for source, capability in SOURCE_CAPABILITIES.items()}


def get_source_capability(source: str) -> dict:
    return asdict(SOURCE_CAPABILITIES[source])
