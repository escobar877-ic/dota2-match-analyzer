"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  Match,
  fetchFromBackend,
  formatMatchDate,
  formatMatchFormat,
  getTeamInitials,
} from "@/lib/api";

type MatchState = {
  matches: Match[];
  loading: boolean;
  error: string | null;
};

export function MatchList() {
  const [state, setState] = useState<MatchState>({
    matches: [],
    loading: true,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;

    async function loadMatches() {
      try {
        const matches = await fetchFromBackend<Match[]>("/matches?limit=24");

        if (!cancelled) {
          setState({ matches, loading: false, error: null });
        }
      } catch {
        if (!cancelled) {
          setState({
            matches: [],
            loading: false,
            error: "Backend is unavailable. Check local server.",
          });
        }
      }
    }

    loadMatches();

    return () => {
      cancelled = true;
    };
  }, []);

  if (state.loading) {
    return (
      <div className="state-card" role="status">
        <span className="loader" aria-hidden="true" />
        <p>Loading matches...</p>
      </div>
    );
  }

  if (state.error) {
    return <div className="state-card state-card-error">{state.error}</div>;
  }

  if (state.matches.length === 0) {
    return (
      <div className="state-card">
        No Tier 1 matches found. Sync data or update Tier 1 allowlist.
      </div>
    );
  }

  return (
    <section className="matches-grid" aria-label="Matches">
      {state.matches.map((match) => (
        <article className="match-card" key={match.id}>
          <div className="card-topline">
            <span className={`badge badge-${match.status}`}>{match.status}</span>
            <span className="badge badge-muted">Tier 1 only</span>
          </div>

          <div className="teams-row">
            <TeamBlock name={match.team_a.name} logoUrl={match.team_a.logo_url} />
            <span className="versus">vs</span>
            <TeamBlock name={match.team_b.name} logoUrl={match.team_b.logo_url} />
          </div>

          <div className="match-meta">
            <span>{match.tournament_name ?? "Tournament TBD"}</span>
            <span>{formatMatchFormat(match.format)}</span>
            <span>{formatMatchDate(match.start_time)}</span>
          </div>

          <Link className="analyze-button" href={`/matches/${match.id}`}>
            Analyze
          </Link>
        </article>
      ))}
    </section>
  );
}

function TeamBlock({ name, logoUrl }: { name: string; logoUrl: string | null }) {
  return (
    <div className="team-block">
      <div className="team-logo" aria-hidden="true">
        {logoUrl ? <img src={logoUrl} alt="" /> : <span>{getTeamInitials(name)}</span>}
      </div>
      <strong>{name}</strong>
    </div>
  );
}
