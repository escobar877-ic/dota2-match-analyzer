"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import {
  DraftFeaturesResponse,
  FormulaPrediction,
  MatchContext,
  MatchDetail,
  MatchDraft,
  MatchForecastHistory,
  MarketEvaluationResponse,
  PredictionExplanation,
  TeamRating,
  PredictionTeamAnalytics,
  BackendApiError,
  fetchFromBackend,
  formatMatchDate,
  formatMatchFormat,
  getTeamInitials,
} from "@/lib/api";

type MatchDetailState = {
  match: MatchDetail | null;
  prediction: FormulaPrediction | null;
  context: MatchContext | null;
  draft: MatchDraft | null;
  draftFeatures: DraftFeaturesResponse | null;
  teamARating: TeamRating | null;
  teamBRating: TeamRating | null;
  forecastHistory: MatchForecastHistory | null;
  loading: boolean;
  error: string | null;
};

export function MatchDetailView() {
  const params = useParams<{ id: string }>();
  const [state, setState] = useState<MatchDetailState>({
    match: null,
    prediction: null,
    context: null,
    draft: null,
    draftFeatures: null,
    teamARating: null,
    teamBRating: null,
    forecastHistory: null,
    loading: true,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;

    async function loadMatch() {
      try {
        const match = await fetchFromBackend<MatchDetail>(`/matches/${params.id}`);
        const verifiedSource = match.verification_status === "verified";
        const strictPrediction = match.is_tier1_match
          ? await fetchOptional<FormulaPrediction>(`/matches/${params.id}/prediction`)
          : null;
        const prediction = strictPrediction ?? (verifiedSource
          ? await fetchOptional<FormulaPrediction>(`/matches/${params.id}/analysis-preview`)
          : null);
        const canLoadContext = match.is_tier1_match || verifiedSource;
        const context = canLoadContext ? await fetchOptional<MatchContext>(`/matches/${params.id}/context`) : null;
        const draft = canLoadContext ? await fetchOptional<MatchDraft>(`/matches/${params.id}/draft`) : null;
        const draftFeatures = canLoadContext
          ? await fetchOptional<DraftFeaturesResponse>(`/matches/${params.id}/draft/features`)
          : null;
        const [teamARating, teamBRating, forecastHistory] = await Promise.all([
          fetchOptional<TeamRating>(`/teams/${match.team_a_id}/rating`),
          fetchOptional<TeamRating>(`/teams/${match.team_b_id}/rating`),
          fetchOptional<MatchForecastHistory>(`/matches/${params.id}/forecast-history`),
        ]);

        if (!cancelled) {
          setState({
            match,
            prediction,
            context,
            draft,
            draftFeatures,
            teamARating,
            teamBRating,
            forecastHistory,
            loading: false,
            error: null,
          });
        }
      } catch (error) {
        if (!cancelled) {
          setState({
            match: null,
            prediction: null,
            context: null,
            draft: null,
            draftFeatures: null,
            teamARating: null,
            teamBRating: null,
            forecastHistory: null,
            loading: false,
            error:
              error instanceof BackendApiError && error.status === 404
                ? "Match not found."
                : "Backend is unavailable. Check local server.",
          });
        }
      }
    }

    loadMatch();

    return () => {
      cancelled = true;
    };
  }, [params.id]);

  if (state.loading) {
    return (
      <main className="page-main">
        <div className="state-card" role="status">
          <span className="loader" aria-hidden="true" />
          <p>Loading match...</p>
        </div>
      </main>
    );
  }

  if (state.error || !state.match) {
    return (
      <main className="page-main">
        <div className="state-card state-card-error">
          {state.error ?? "Match not found."}
          <Link className="back-link" href="/">
            Back to matches
          </Link>
        </div>
      </main>
    );
  }

  const match = state.match;
  const prediction = state.prediction;
  const verifiedAnalytics = prediction?.prediction_type === "verified_pro_preview"
    ? prediction.analytics_context ?? null
    : null;

  return (
    <main className="page-main">
      <div className="detail-shell">
        <Link className="back-link" href="/">
          Back to matches
        </Link>

        <section className="detail-hero">
          <TeamHero name={match.team_a.name} logoUrl={match.team_a.logo_url} />
          <div className="detail-versus">
            <span className={`badge badge-${match.status}`}>{match.status}</span>
            <strong>vs</strong>
            <span className="badge badge-muted">{formatMatchScope(match)}</span>
          </div>
          <TeamHero name={match.team_b.name} logoUrl={match.team_b.logo_url} />
        </section>

        <section className="detail-grid" aria-label="Match details">
          <InfoTile label="Tournament" value={match.tournament_name ?? "Tournament TBD"} />
          <InfoTile label="Tier" value={formatCompetitionTier(match)} />
          <InfoTile label="Time" value={formatMatchDate(match.start_time)} />
          <InfoTile label="Format" value={formatMatchFormat(match.format)} />
          <InfoTile label="Status" value={match.status} />
          <InfoTile
            label={`${match.team_a.name} ${verifiedAnalytics ? "Pro Elo" : "Elo"}`}
            value={formatAnalysisRating(verifiedAnalytics?.team_a, state.teamARating)}
          />
          <InfoTile
            label={`${match.team_b.name} ${verifiedAnalytics ? "Pro Elo" : "Elo"}`}
            value={formatAnalysisRating(verifiedAnalytics?.team_b, state.teamBRating)}
          />
        </section>

        {match.status === "finished" ? (
          <MatchResultPanel match={match} forecastHistory={state.forecastHistory} />
        ) : null}

        {!match.is_tier1_match && !prediction ? (
          <section className="prediction-placeholder">
            <p className="panel-label">Excluded</p>
            <h2>{match.excluded_reason ?? "This match is excluded from Tier 1 analysis."}</h2>
          </section>
        ) : prediction ? (
          <>
            {!match.is_tier1_match ? <VerifiedProNotice match={match} /> : null}
            {!match.is_tier1_match ? <VerifiedProDataPanel match={match} prediction={prediction} /> : null}
            <ContextPanel match={match} context={state.context} />
            <DraftPanel draft={state.draft} draftFeatures={state.draftFeatures} />
            <PredictionPanel match={match} prediction={prediction} />
          </>
        ) : (
          <section className="prediction-placeholder">
            <p className="panel-label">Prediction</p>
            <h2>Prediction will be available here</h2>
          </section>
        )}
      </div>
    </main>
  );
}

function VerifiedProNotice({ match }: { match: MatchDetail }) {
  return (
    <section className="prediction-placeholder">
      <p className="panel-label">Verified pro preview</p>
      <h2>{match.competition_tier ?? "pro"} match, strict prediction blocked</h2>
      <p className="prediction-warning">
        This row is verified by {match.external_source ?? "source"}, but one or both teams are outside the strict Tier 1
        allowlist. The preview below is cautious context only and is not used for training, promotion, or automated betting.
      </p>
      {match.status === "live" ? (
        <p className="prediction-warning">Live score and in-game state are not included. This remains a pre-match baseline.</p>
      ) : null}
      {match.prediction_block_reason || match.excluded_reason ? (
        <p className="confidence-line">
          Block reason: <strong>{match.prediction_block_reason ?? match.excluded_reason}</strong>
        </p>
      ) : null}
    </section>
  );
}

async function fetchOptional<T>(path: string): Promise<T | null> {
  try {
    return await fetchFromBackend<T>(path);
  } catch {
    return null;
  }
}

function formatElo(rating: TeamRating | null): string {
  if (!rating) {
    return "Elo TBD";
  }
  return `${rating.rating_value} (${rating.matches_count} matches)`;
}

function formatMatchScope(match: MatchDetail): string {
  if (match.is_tier1_match) {
    return "Tier 1";
  }
  return match.competition_tier === "pro" ? "Verified pro" : "Excluded";
}

function formatCompetitionTier(match: MatchDetail): string {
  if (match.tournament_tier) {
    return match.tournament_tier;
  }
  if (match.competition_tier === "tier1" || match.is_tier1_match) {
    return "Tier 1";
  }
  if (match.competition_tier === "pro") {
    return "Verified pro";
  }
  return "Tier TBD";
}

function formatAnalysisRating(analytics: PredictionTeamAnalytics | undefined, fallback: TeamRating | null): string {
  if (analytics && typeof analytics.elo_rating === "number") {
    return `${Math.round(analytics.elo_rating)} (${analytics.matches_count} past matches)`;
  }
  return formatElo(fallback);
}

function formatFeatureValue(value: number | string | boolean | null | undefined): string {
  if (typeof value === "number") {
    return value.toFixed(3);
  }
  if (typeof value === "boolean") {
    return value ? "yes" : "no";
  }
  return value ? String(value) : "N/A";
}

function TeamHero({ name, logoUrl }: { name: string; logoUrl: string | null }) {
  return (
    <div className="team-hero">
      <div className="team-logo team-logo-large" aria-hidden="true">
        {logoUrl ? <img src={logoUrl} alt="" /> : <span>{getTeamInitials(name)}</span>}
      </div>
      <h1>{name}</h1>
    </div>
  );
}

function InfoTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="info-tile">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function MatchResultPanel({
  match,
  forecastHistory,
}: {
  match: MatchDetail;
  forecastHistory: MatchForecastHistory | null;
}) {
  const snapshot = forecastHistory?.preferred_snapshot ?? null;
  const result = match.is_draw
    ? "Draw"
    : forecastHistory?.winner_team_name
      ?? (match.winner_team_id === match.team_a_id
        ? match.team_a.name
        : match.winner_team_id === match.team_b_id
          ? match.team_b.name
          : "Winner unavailable");

  return (
    <section className="prediction-placeholder context-panel" aria-label="Match result and captured forecast">
      <div>
        <p className="panel-label">Result</p>
        <h2>{result}</h2>
      </div>
      {snapshot ? (
        <>
          <div className="context-grid">
            <InfoTile label="Captured horizon" value={snapshot.horizon_bucket.replaceAll("_", " ")} />
            <InfoTile label={match.team_a.name} value={`${Math.round(snapshot.team_a_probability * 100)}%`} />
            <InfoTile label={match.team_b.name} value={`${Math.round(snapshot.team_b_probability * 100)}%`} />
            <InfoTile label="Forecast result" value={snapshot.correct === null ? snapshot.status : snapshot.correct ? "Correct" : "Incorrect"} />
          </div>
          <p className="confidence-line">
            Captured {formatMatchDate(snapshot.generated_at)} with {snapshot.lead_time_hours.toFixed(1)} hours lead time.
          </p>
        </>
      ) : (
        <p className="prediction-warning">
          No prospective pre-match snapshot was captured for this match. The model estimate below is retrospective and is
          excluded from prospective accuracy metrics.
        </p>
      )}
    </section>
  );
}

function VerifiedProDataPanel({
  match,
  prediction,
}: {
  match: MatchDetail;
  prediction: FormulaPrediction;
}) {
  const context = prediction.analytics_context;
  if (!context) {
    return null;
  }

  return (
    <section className="prediction-placeholder context-panel" aria-label="Verified pro data scope">
      <div>
        <p className="panel-label">Analysis data</p>
        <h2>Verified cross-source history</h2>
      </div>
      <div className="context-grid">
        <InfoTile label={`${match.team_a.name} history`} value={`${context.team_a.matches_count} matches`} />
        <InfoTile label={`${match.team_b.name} history`} value={`${context.team_b.matches_count} matches`} />
        <InfoTile label={`${match.team_a.name} weighted form`} value={formatRate(context.team_a.recent_form)} />
        <InfoTile label={`${match.team_b.name} weighted form`} value={formatRate(context.team_b.recent_form)} />
        <InfoTile label="Head-to-head sample" value={`${context.head_to_head_matches} matches`} />
        <InfoTile label="Time guard" value={context.uses_only_past_matches ? "Past matches only" : "Limited"} />
      </div>
      <p className="prediction-warning">
        Exact normalized team identities are joined across verified sources. Dev seed and future results are excluded.
      </p>
    </section>
  );
}

function formatRate(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function ContextPanel({ match, context }: { match: MatchDetail; context: MatchContext | null }) {
  if (!context) {
    return null;
  }

  return (
    <section className="prediction-placeholder context-panel" aria-label="Match context">
      <div>
        <p className="panel-label">Context</p>
        <h2>{context.patch ? `Patch ${context.patch.patch_version}` : "Patch unknown"}</h2>
      </div>
      <div className="context-grid">
        <InfoTile label="Days since patch" value={context.days_since_patch?.toString() ?? "N/A"} />
        <InfoTile label={match.team_a.name} value={formatRosterContext(context.teams.team_a)} />
        <InfoTile label={match.team_b.name} value={formatRosterContext(context.teams.team_b)} />
      </div>
      {context.teams.team_a.has_recent_roster_change || context.teams.team_b.has_recent_roster_change ? (
        <p className="prediction-warning">Recent roster change detected for one or both teams.</p>
      ) : null}
    </section>
  );
}

function formatRosterContext(context: MatchContext["teams"]["team_a"]): string {
  if (context.roster_ambiguous) {
    return `${context.roster_count} active / ambiguous`;
  }
  if (context.roster_stability_known && context.roster_stability_days !== null) {
    return `${context.roster_stability_days} roster days`;
  }
  if (context.roster_known) {
    return "5 active players";
  }
  if (context.roster_count > 0) {
    return `${context.roster_count}/5 players tracked`;
  }
  return "Roster unknown";
}

function DraftPanel({ draft, draftFeatures }: { draft: MatchDraft | null; draftFeatures: DraftFeaturesResponse | null }) {
  if (!draft) {
    return null;
  }

  const features = draftFeatures?.features;

  return (
    <section className="prediction-placeholder context-panel" aria-label="Draft context">
      <div>
        <p className="panel-label">Draft</p>
        <h2>{draft.draft_available ? (draft.draft_complete ? "Draft complete" : "Partial draft") : "Draft unavailable"}</h2>
      </div>
      <div className="context-grid">
        <InfoTile label="Team A picks" value={String(draft.team_a_picks_count)} />
        <InfoTile label="Team B picks" value={String(draft.team_b_picks_count)} />
        <InfoTile label="Team A bans" value={String(draft.team_a_bans_count)} />
        <InfoTile label="Team B bans" value={String(draft.team_b_bans_count)} />
      </div>
      {features ? (
        <div className="context-grid">
          <InfoTile label="Hero comfort diff" value={formatFeatureValue(features.hero_pool_comfort_diff)} />
          <InfoTile label="Patch hero diff" value={formatFeatureValue(features.patch_hero_winrate_diff)} />
          <InfoTile label="Draft synergy diff" value={formatFeatureValue(features.draft_synergy_diff)} />
        </div>
      ) : null}
      <p className="prediction-warning">Draft features are experimental and not used in main prediction yet.</p>
    </section>
  );
}

function PredictionPanel({
  match,
  prediction,
}: {
  match: MatchDetail;
  prediction: FormulaPrediction;
}) {
  const teamAPercent = Math.round(prediction.team_a_probability * 100);
  const teamBPercent = Math.round(prediction.team_b_probability * 100);
  const probabilityLabel = prediction.probability_unit === "map_strength" ? "map strength" : "win probability";
  const hasComponents = Boolean(prediction.components && Object.keys(prediction.components).length > 0);

  return (
    <section className="prediction-panel" aria-label="Match prediction">
      <div className="prediction-header">
        <div>
          <p className="panel-label">{match.status === "finished" ? "Retrospective estimate" : "Baseline prediction"}</p>
          <h2>{predictionTitle(prediction)}</h2>
        </div>
        <span className={`confidence-badge confidence-${prediction.confidence}`}>
          {predictionBadge(prediction)}
        </span>
      </div>

      {match.status === "finished" ? (
        <p className="prediction-warning">
          This estimate uses the current active model with features cut off before match start. Only a separately captured
          forecast snapshot counts as prospective evidence.
        </p>
      ) : null}

      <div className="confidence-line">
        Model version: <strong>{prediction.model_version}</strong>
      </div>
      {prediction.fallback_used && prediction.fallback_reason ? (
        <div className="confidence-line">
          Using fallback: <strong>{prediction.fallback_reason}</strong>
        </div>
      ) : null}

      <div className="probability-grid">
        <ProbabilityBar
          teamName={match.team_a.name}
          value={prediction.team_a_probability}
          percent={teamAPercent}
          probabilityLabel={probabilityLabel}
          align="left"
        />
        <ProbabilityBar
          teamName={match.team_b.name}
          value={prediction.team_b_probability}
          percent={teamBPercent}
          probabilityLabel={probabilityLabel}
          align="right"
        />
      </div>

      {prediction.probability_unit === "map_strength" ? (
        <p className="confidence-line">
          Per-map strength estimate. Use the series outcomes below for BO2/BO3/BO5 markets.
        </p>
      ) : null}

      {prediction.series_outcomes ? (
        <SeriesOutcomesPanel match={match} prediction={prediction} />
      ) : null}

      {match.status === "upcoming" ? (
        <OddsEvaluationPanel match={match} prediction={prediction} />
      ) : (
        <p className="prediction-warning">
          Market and paper evaluation are disabled after match start to prevent hindsight-biased records.
        </p>
      )}

      <div className="confidence-line">
        Confidence score: <strong>{Math.round(prediction.confidence_score * 100)}%</strong>
      </div>

      {prediction.confidence_guard_applied ? (
        <div className="confidence-guard">
          <strong>Confidence guard adjusted this prediction.</strong>
          {prediction.confidence_reasons?.length ? (
            <ul className="explanation-list">
              {prediction.confidence_reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {hasComponents ? <EnsembleComponentsPanel prediction={prediction} /> : null}

      <div className="factor-grid">
        {Object.entries(prediction.factors).map(([name, value]) => (
          <div className="factor-tile" key={name}>
            <span>{name.replaceAll("_", " ")}</span>
            <strong>{value > 0 ? "+" : ""}{value.toFixed(2)}</strong>
          </div>
        ))}
      </div>

      <PredictionExplanationPanel prediction={prediction} />

      <p className="prediction-warning">{prediction.warning}</p>
    </section>
  );
}

function OddsEvaluationPanel({
  match,
  prediction,
}: {
  match: MatchDetail;
  prediction: FormulaPrediction;
}) {
  const [bookmaker, setBookmaker] = useState("manual");
  const [teamAOdds, setTeamAOdds] = useState("");
  const [drawOdds, setDrawOdds] = useState("");
  const [teamBOdds, setTeamBOdds] = useState("");
  const [result, setResult] = useState<MarketEvaluationResponse | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hasDraw = Boolean(prediction.series_outcomes?.draw);

  async function evaluate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const response = await fetchFromBackend<MarketEvaluationResponse>(
        `/matches/${match.id}/odds/evaluate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            bookmaker,
            market_type: "auto",
            team_a_odds: Number(teamAOdds),
            team_b_odds: Number(teamBOdds),
            draw_odds: hasDraw ? Number(drawOdds) : null,
          }),
        },
      );
      setResult(response);
    } catch {
      setError("Could not evaluate these odds. Check that every decimal price is above 1.00.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="odds-evaluation" aria-label="Paper value evaluation">
      <div className="ensemble-components-header">
        <div>
          <p className="panel-label">Paper tracking</p>
          <h3>Market value</h3>
        </div>
        <span className="confidence-badge confidence-medium">No real bet</span>
      </div>
      <form className="odds-form" onSubmit={evaluate}>
        <label>
          <span>Bookmaker</span>
          <input value={bookmaker} onChange={(event) => setBookmaker(event.target.value)} required />
        </label>
        <label>
          <span>{match.team_a.name}</span>
          <input type="number" min="1.01" step="0.01" value={teamAOdds} onChange={(event) => setTeamAOdds(event.target.value)} required />
        </label>
        {hasDraw ? (
          <label>
            <span>Draw</span>
            <input type="number" min="1.01" step="0.01" value={drawOdds} onChange={(event) => setDrawOdds(event.target.value)} required />
          </label>
        ) : null}
        <label>
          <span>{match.team_b.name}</span>
          <input type="number" min="1.01" step="0.01" value={teamBOdds} onChange={(event) => setTeamBOdds(event.target.value)} required />
        </label>
        <button className="analyze-button" type="submit" disabled={submitting}>
          {submitting ? "Evaluating..." : "Evaluate paper value"}
        </button>
      </form>
      {error ? <p className="prediction-warning">{error}</p> : null}
      {result ? (
        <div className="odds-result">
          <div className="confidence-line">
            Bookmaker margin: <strong>{Math.max(0, (result.overround - 1) * 100).toFixed(1)}%</strong>
          </div>
          <div className="series-outcome-grid">
            {result.outcomes.map((outcome) => (
              <div className="series-outcome-item" key={outcome.outcome}>
                <span>{outcomeLabel(outcome.outcome, match)}</span>
                <strong>EV {formatSignedPercent(outcome.expected_value)}</strong>
                <small>Edge {formatSignedPercent(outcome.edge)}</small>
              </div>
            ))}
          </div>
          <p className={result.paper_test_eligible ? "paper-status paper-status-ok" : "prediction-warning"}>
            {result.paper_test_eligible
              ? `Paper test recorded${result.paper_bet_id ? ` #${result.paper_bet_id}` : ""}.`
              : "No paper test recorded."}
          </p>
          {result.guard_reasons.length ? (
            <ul className="explanation-list">
              {result.guard_reasons.map((reason) => <li key={reason}>{reason}</li>)}
            </ul>
          ) : null}
          <p className="prediction-warning">{result.warning}</p>
        </div>
      ) : null}
    </section>
  );
}

function outcomeLabel(outcome: string, match: MatchDetail): string {
  if (outcome === "team_a") return match.team_a.name;
  if (outcome === "team_b") return match.team_b.name;
  return "Draw";
}

function formatSignedPercent(value: number): string {
  const percent = value * 100;
  return `${percent >= 0 ? "+" : ""}${percent.toFixed(1)}%`;
}

function SeriesOutcomesPanel({
  match,
  prediction,
}: {
  match: MatchDetail;
  prediction: FormulaPrediction;
}) {
  const outcomes = prediction.series_outcomes;
  if (!outcomes) {
    return null;
  }
  const isBo2 = outcomes.format === "BO2";
  const rows = [
    {
      label: isBo2 ? `${match.team_a.name} 2-0` : `${match.team_a.name} wins series`,
      value: outcomes.team_a_win,
    },
    ...(isBo2 ? [{ label: "Draw 1-1", value: outcomes.draw }] : []),
    {
      label: isBo2 ? `${match.team_b.name} 2-0` : `${match.team_b.name} wins series`,
      value: outcomes.team_b_win,
    },
  ];

  return (
    <section className="series-outcomes" aria-label={`${outcomes.format} series outcomes`}>
      <div className="ensemble-components-header">
        <div>
          <p className="panel-label">Series market</p>
          <h3>{outcomes.format} outcome probabilities</h3>
        </div>
        <span className="confidence-badge confidence-medium">Derived</span>
      </div>
      <div className="series-outcome-grid">
        {rows.map((row) => (
          <div className="series-outcome-item" key={row.label}>
            <span>{row.label}</span>
            <strong>{Math.round(row.value * 1000) / 10}%</strong>
          </div>
        ))}
      </div>
      <p className="prediction-warning">{outcomes.assumption_warning}</p>
    </section>
  );
}

function predictionTitle(prediction: FormulaPrediction): string {
  if (prediction.prediction_type === "verified_pro_preview") {
    return prediction.probability_unit === "map_strength"
      ? "Verified pro map-strength preview"
      : "Verified pro pre-match preview";
  }
  if (prediction.probability_unit === "map_strength") {
    return prediction.prediction_type === "ensemble" ? "Ensemble map strength" : "Per-map strength estimate";
  }
  if (prediction.prediction_type === "ensemble") {
    return "Ensemble win probability";
  }
  if (prediction.prediction_type === "ml") {
    return "ML win probability";
  }
  return "Formula win probability";
}

function predictionBadge(prediction: FormulaPrediction): string {
  if (prediction.prediction_type === "ensemble") {
    return "Ensemble prediction";
  }
  if (prediction.prediction_type === "verified_pro_preview") {
    return "Cautious preview";
  }
  if (prediction.prediction_type === "ml") {
    return "ML model";
  }
  return prediction.fallback_used ? "Formula fallback" : "Formula";
}

function EnsembleComponentsPanel({ prediction }: { prediction: FormulaPrediction }) {
  if (!prediction.components) {
    return null;
  }

  return (
    <section className="ensemble-components" aria-label="Ensemble prediction components">
      <div className="ensemble-components-header">
        <div>
          <p className="panel-label">Components</p>
          <h3>Formula, Elo and ML weights</h3>
        </div>
        <strong>{prediction.confidence} confidence</strong>
      </div>

      {prediction.weight_source ? (
        <p className="ensemble-weight-source">
          {prediction.weight_source === "walk_forward"
            ? "Weights approved by walk-forward validation"
            : prediction.weight_source === "backtest"
              ? "Weights based on latest backtest"
              : "Default ensemble weights"}
          {prediction.weight_reason ? `: ${prediction.weight_reason}` : null}
        </p>
      ) : null}

      <div className="ensemble-component-grid">
        {(["formula", "elo", "ml"] as const).map((name) => {
          const component = prediction.components?.[name];
          if (!component) {
            return null;
          }

          return (
            <div className={`ensemble-component-card ${component.available ? "" : "ensemble-component-muted"}`} key={name}>
              <span>{name.toUpperCase()}</span>
              <strong>
                {component.available && typeof component.team_a_probability === "number"
                  ? `${Math.round(component.team_a_probability * 100)}%`
                  : "Unavailable"}
              </strong>
              <small>Weight {Math.round(component.weight * 100)}%</small>
              {component.model_version ? <small>{component.model_version}</small> : null}
              {!component.available && component.unavailable_reason ? <small>{component.unavailable_reason}</small> : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function PredictionExplanationPanel({ prediction }: { prediction: FormulaPrediction }) {
  const explanation = prediction.explanation;
  const isMlPrediction = prediction.prediction_type === "ml";
  const isEnsemblePrediction = prediction.prediction_type === "ensemble";
  const hasComponents = Boolean(prediction.components && Object.keys(prediction.components).length > 0);
  const explanationTitle = isEnsemblePrediction
    ? "Ensemble explanation"
    : hasComponents
      ? "Component explanation"
      : isMlPrediction
        ? "ML explanation"
        : "Prediction explanation";
  const explanationBadge = isEnsemblePrediction
    ? "Ensemble prediction"
    : prediction.prediction_type === "verified_pro_preview"
      ? "Cautious preview"
      : isMlPrediction
        ? "ML explanation"
        : "Formula explanation";

  if (Array.isArray(explanation)) {
    return (
      <section className="prediction-explanation" aria-label="Prediction explanation">
        <div className="explanation-header">
          <div>
            <p className="panel-label">Why this prediction?</p>
            <h3>Formula explanation</h3>
          </div>
          <span className={`confidence-badge confidence-${prediction.confidence}`}>
            {prediction.fallback_used ? "Formula fallback" : "Formula explanation"}
          </span>
        </div>
        {explanation.length > 0 ? (
          <ul className="explanation-list">
            {explanation.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        ) : (
          <p className="explanation-empty">No detailed explanation available for this prediction.</p>
        )}
      </section>
    );
  }

  if (!isPredictionExplanation(explanation)) {
    return (
      <section className="prediction-explanation" aria-label="Prediction explanation">
        <div className="explanation-header">
          <div>
            <p className="panel-label">Why this prediction?</p>
            <h3>{explanationTitle}</h3>
          </div>
          <span className={`confidence-badge confidence-${prediction.confidence}`}>
            {explanationBadge}
          </span>
        </div>
        <p className="explanation-empty">No detailed explanation available for this prediction.</p>
      </section>
    );
  }

  const positiveFactors = explanation.positive_factors ?? [];
  const negativeFactors = explanation.negative_factors ?? [];
  const rawFeatureEntries = Object.entries(explanation.raw_feature_values ?? {});
  const hasLimitedFactors = positiveFactors.length === 0 && negativeFactors.length === 0;

  return (
    <section className="prediction-explanation" aria-label="Prediction explanation">
      <div className="explanation-header">
        <div>
          <p className="panel-label">Why this prediction?</p>
          <h3>{explanationTitle}</h3>
        </div>
        <span className={`confidence-badge confidence-${prediction.confidence}`}>
          {explanationBadge}
        </span>
      </div>

      {explanation.summary ? <p className="explanation-summary">{explanation.summary}</p> : null}

      {explanation.component_summary && explanation.component_summary.length > 0 ? (
        <ul className="explanation-list component-summary-list">
          {explanation.component_summary.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : null}

      {hasLimitedFactors ? (
        <p className="explanation-empty">
          Explanation is limited because not enough feature data is available.
        </p>
      ) : (
        <div className="explanation-factor-grid">
          <FactorList title="Main reasons" tone="positive" factors={positiveFactors} />
          <FactorList title="Risks" tone="negative" factors={negativeFactors} />
        </div>
      )}

      {rawFeatureEntries.length > 0 ? (
        <details className="raw-features">
          <summary>Raw feature values</summary>
          <pre>{JSON.stringify(explanation.raw_feature_values, null, 2)}</pre>
        </details>
      ) : null}
    </section>
  );
}

function FactorList({
  title,
  tone,
  factors,
}: {
  title: string;
  tone: "positive" | "negative";
  factors: NonNullable<PredictionExplanation["positive_factors"]>;
}) {
  return (
    <div className={`explanation-factor-card explanation-factor-card-${tone}`}>
      <h4>{title}</h4>
      {factors.length > 0 ? (
        <ul className="explanation-list explanation-factor-list">
          {factors.map((factor) => (
            <li key={`${factor.factor}-${factor.impact}`}>
              <span>{factor.text}</span>
              <strong>{formatImpact(factor.impact)}</strong>
            </li>
          ))}
        </ul>
      ) : (
        <p className="explanation-empty">No factors available.</p>
      )}
    </div>
  );
}

function isPredictionExplanation(value: FormulaPrediction["explanation"]): value is PredictionExplanation {
  return Boolean(value) && !Array.isArray(value) && typeof value === "object";
}

function formatImpact(value: number): string {
  const percentage = Math.round(Math.abs(value) * 100);
  return `${value >= 0 ? "+" : "-"}${percentage}%`;
}

function ProbabilityBar({
  teamName,
  value,
  percent,
  probabilityLabel,
  align,
}: {
  teamName: string;
  value: number;
  percent: number;
  probabilityLabel: string;
  align: "left" | "right";
}) {
  return (
    <div className={`probability-card probability-${align}`}>
      <div className="probability-label">
        <span>{teamName}</span>
        <strong>{percent}%</strong>
      </div>
      <div className="probability-track" aria-label={`${teamName} ${probabilityLabel} ${percent}%`}>
        <span style={{ width: `${Math.round(value * 100)}%` }} />
      </div>
    </div>
  );
}
