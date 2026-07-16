import Link from "next/link";

import { LocalDateTime } from "@/components/local-date-time";
import { UpcomingMatchSearchResponse, fetchFromBackend } from "@/lib/api";
import { formatPredictionGuardReasons } from "@/lib/prediction-guards";

type SearchParams = {
  q?: string;
  team?: string;
  tournament?: string;
  source?: string;
  prediction_eligible?: string;
  analysis_scope?: string;
};

export default async function UpcomingPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = await searchParams;
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) {
      query.set(key, value);
    }
  }
  if (!query.has("limit")) {
    query.set("limit", "100");
  }
  query.set("include_prediction", "true");
  const selectedScope = params.analysis_scope ?? (params.prediction_eligible === "true" ? "strict" : "all");
  query.delete("prediction_eligible");
  query.set("analysis_scope", selectedScope);
  const data = await fetchOptional<UpcomingMatchSearchResponse>(`/matches/upcoming?${query.toString()}`);
  const tournamentOptions = data?.tournament_options ?? [];
  const selectedTournament = params.tournament ?? "";
  const hasSelectedTournament = tournamentOptions.some((option) => option.name === selectedTournament);
  const hasActiveFilters = Boolean(
    params.q || params.team || params.tournament || params.source || selectedScope !== "all",
  );

  return (
    <main className="page-main">
      <section className="home-shell" aria-label="Upcoming matches">
        <div className="page-header">
          <div>
            <p className="eyebrow">Schedule</p>
            <h1>Upcoming</h1>
          </div>
          <p className="subtitle">Choose a tournament to see all of its live and upcoming matches. Prediction and training guards stay strict.</p>
        </div>

        <form className="prediction-placeholder data-coverage-panel" action="/upcoming">
          <p className="panel-label">Search</p>
          <div className="data-source-grid coverage-grid">
            <label className="info-tile">
              <span>Search</span>
              <input name="q" defaultValue={params.q ?? ""} placeholder="team or tournament" />
            </label>
            <label className="info-tile">
              <span>Team</span>
              <input name="team" defaultValue={params.team ?? ""} placeholder="Team Liquid" />
            </label>
            <label className="info-tile">
              <span>Tournament</span>
              <select name="tournament" defaultValue={selectedTournament}>
                <option value="">All upcoming tournaments</option>
                {selectedTournament && !hasSelectedTournament ? (
                  <option value={selectedTournament}>{selectedTournament}</option>
                ) : null}
                {tournamentOptions.map((option) => (
                  <option key={option.name} value={option.name}>
                    {formatTournamentOption(option)}
                  </option>
                ))}
              </select>
            </label>
            <label className="info-tile">
              <span>Source</span>
              <select name="source" defaultValue={params.source ?? ""}>
                <option value="">All</option>
                <option value="pandascore">PandaScore</option>
                <option value="stratz">STRATZ</option>
                <option value="opendota">OpenDota</option>
              </select>
            </label>
          </div>
          <label className="info-tile">
            <span>Match scope</span>
            <select name="analysis_scope" defaultValue={selectedScope}>
              <option value="all">All matches</option>
              <option value="actionable">Strict + verified preview</option>
              <option value="strict">Strict prediction only</option>
              <option value="preview">Verified previews only</option>
            </select>
          </label>
          <div className="filter-actions">
            <button className="analyze-button" type="submit">Show matches</button>
            {hasActiveFilters ? <Link className="filter-reset" href="/upcoming">Reset</Link> : null}
          </div>
          <p>Sync hint: bash scripts/sync_upcoming_matches.sh --source pandascore --limit 50</p>
        </form>

        <section className="detail-grid" aria-label="Upcoming analysis coverage">
          <div className="info-tile">
            <span>Strict predictions</span>
            <strong>{data?.scope_summary?.strict_prediction_count ?? 0}</strong>
          </div>
          <div className="info-tile">
            <span>Verified previews</span>
            <strong>{data?.scope_summary?.verified_pro_preview_count ?? 0}</strong>
          </div>
          <div className="info-tile">
            <span>Blocked</span>
            <strong>{data?.scope_summary?.blocked_count ?? 0}</strong>
          </div>
          <div className="info-tile">
            <span>Training rows</span>
            <strong>{data?.scope_summary?.training_eligible_count ?? 0}</strong>
          </div>
        </section>

        <section className="matches-grid" aria-label="Upcoming match results">
          {data?.items?.length ? (
            data.items.map((match) => {
              const decisionReasons = match.decision_status === "blocked"
                ? formatPredictionGuardReasons(match.prediction_block_reason, {
                    teamAName: match.team_a?.name,
                    teamBName: match.team_b?.name,
                    tournamentName: match.tournament,
                    status: match.status,
                  })
                : (match.decision_reasons?.length
                    ? match.decision_reasons
                    : [match.decision_reason ?? "Open match detail for full context."]);
              return <article
                className={`match-card ${match.preview_eligible ? "match-card-preview" : match.prediction_eligible ? "match-card-strict" : ""}`}
                key={match.id}
              >
                <div className="match-card-header">
                  <span className={`badge badge-${match.status}`}>{match.status}</span>
                  <span className="badge badge-muted">{match.source ?? "unknown"}</span>
                  <span className={match.preview_eligible ? "badge badge-upcoming" : "badge badge-muted"}>
                    {match.preview_eligible ? "verified preview" : match.prediction_eligible ? "strict" : "blocked"}
                  </span>
                  <span className={decisionBadgeClass(match.decision_status)}>{decisionLabel(match.decision_status)}</span>
                  <span className="match-date"><LocalDateTime value={match.start_time} /></span>
                </div>
                <h2>{match.team_a?.name ?? "TBD"} vs {match.team_b?.name ?? "TBD"}</h2>
                <p>{match.tournament ?? "Unknown tournament"} {match.format ? `- ${match.format}` : ""}</p>
                {match.prediction_summary ? (
                  <div className="detail-grid">
                    <div className="info-tile">
                      <span>
                        {match.team_a?.name ?? "Team A"}
                        {match.prediction_summary.probability_unit === "map_strength" ? " map strength" : ""}
                      </span>
                      <strong>{formatPercent(match.prediction_summary.team_a_probability)}</strong>
                    </div>
                    <div className="info-tile">
                      <span>
                        {match.team_b?.name ?? "Team B"}
                        {match.prediction_summary.probability_unit === "map_strength" ? " map strength" : ""}
                      </span>
                      <strong>{formatPercent(match.prediction_summary.team_b_probability)}</strong>
                    </div>
                    <div className="info-tile">
                      <span>Confidence</span>
                      <strong>{match.prediction_summary.confidence}</strong>
                    </div>
                    <div className="info-tile">
                      <span>Weights</span>
                      <strong>{match.prediction_summary.weight_source ?? "default"}</strong>
                    </div>
                  </div>
                ) : null}
                {match.prediction_summary?.series_outcomes ? (
                  <p className="confidence-line">
                    Series outcomes: {match.team_a?.name ?? "Team A"}{" "}
                    {formatPercent(match.prediction_summary.series_outcomes.team_a_win)}
                    {match.prediction_summary.series_outcomes.draw > 0
                      ? ` / draw ${formatPercent(match.prediction_summary.series_outcomes.draw)}`
                      : ""}
                    {" / "}{match.team_b?.name ?? "Team B"}{" "}
                    {formatPercent(match.prediction_summary.series_outcomes.team_b_win)}
                  </p>
                ) : null}
                <p>
                  Decision: <strong>{decisionLabel(match.decision_status)}</strong>.
                </p>
                {decisionReasons.length ? (
                  <div className="prediction-warning">
                    {decisionReasons.slice(0, 3).map((reason) => (
                      <p key={reason}>{reason}</p>
                    ))}
                  </div>
                ) : null}
                <p>
                  Verification: <strong>{match.verification_status}</strong>. Confidence:{" "}
                  <strong>{match.source_confidence}</strong>.
                </p>
                <p>
                  Competition: <strong>{match.competition_tier ?? "unknown"}</strong>. Guard:{" "}
                  <strong>{match.prediction_guard_level ?? "high"}</strong>.
                </p>
                {match.preview_eligible ? (
                  <p className="prediction-warning">
                    Verified pro preview only. Guarded components may be shown, but strict prediction, training, promotion and betting automation remain disabled.
                  </p>
                ) : null}
                {match.prediction_eligible ? (
                  <Link className="analyze-button" href={`/matches/${match.id}`}>
                    {match.decision_status === "needs_odds" ? "Open odds check" : "Open prediction"}
                  </Link>
                ) : match.preview_eligible ? (
                  <Link className="analyze-button" href={`/matches/${match.id}`}>
                    Open verified preview
                  </Link>
                ) : null}
              </article>
            })
          ) : (
            <section className="prediction-placeholder">
              <p>No upcoming matches found. Run PandaScore upcoming dry-run/apply after reviewing source health.</p>
            </section>
          )}
        </section>
      </section>
    </main>
  );
}

async function fetchOptional<T>(path: string): Promise<T | null> {
  try {
    return await fetchFromBackend<T>(path);
  } catch {
    return null;
  }
}

function decisionLabel(status: string | null | undefined): string {
  if (status === "needs_odds") return "Needs odds";
  if (status === "watch") return "Watch";
  if (status === "skip") return "Skip";
  if (status === "preview") return "Preview";
  if (status === "blocked") return "Blocked";
  return "Review";
}

function decisionBadgeClass(status: string | null | undefined): string {
  if (status === "needs_odds") return "badge badge-muted";
  if (status === "preview" || status === "skip" || status === "blocked") return "badge badge-upcoming";
  return "badge";
}

function formatPercent(value: number | null | undefined): string {
  return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "N/A";
}

function formatTournamentOption(option: NonNullable<UpcomingMatchSearchResponse["tournament_options"]>[number]): string {
  const statusCounts = [
    option.live_count ? `${option.live_count} live` : null,
    option.upcoming_count ? `${option.upcoming_count} upcoming` : null,
  ].filter(Boolean);
  return `${option.name} (${statusCounts.join(", ") || `${option.match_count} matches`})`;
}
