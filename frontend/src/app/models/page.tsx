import {
  Backtest,
  DraftExperimentReport,
  ForecastHealthReport,
  ModelVersion,
  PaperBetsSummary,
  ProspectiveAccuracyReport,
  ProspectiveDecisionReport,
  fetchFromBackend,
} from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function ModelsPage() {
  const [
    models,
    activeModelResponse,
    candidateModels,
    latestBacktest,
    draftExperiments,
    prospective,
    prospectiveDecision,
    forecastHealth,
    paperBets,
  ] = await Promise.all([
    fetchOptional<ModelVersion[]>("/models"),
    fetchOptional<ModelVersion | null>("/models/active"),
    fetchOptional<ModelVersion[]>("/models/candidates"),
    fetchOptional<Backtest | null>("/backtests/latest"),
    fetchOptional<DraftExperimentReport>("/models/draft-experiments"),
    fetchOptional<ProspectiveAccuracyReport>("/models/prospective-accuracy"),
    fetchOptional<ProspectiveDecisionReport>("/models/prospective-decision"),
    fetchOptional<ForecastHealthReport>("/models/forecast-health"),
    fetchOptional<PaperBetsSummary>("/paper-bets/summary"),
  ]);
  const activeModel = activeModelResponse ?? models?.find((model) => model.is_active) ?? null;
  const candidates = candidateModels ?? models?.filter((model) => model.status === "candidate" && !model.is_active) ?? [];
  const report = latestBacktest?.metrics_json as QualityReport | null | undefined;

  return (
    <main className="page-main">
      <section className="home-shell" aria-label="Model quality">
        <div className="page-header">
          <div>
            <p className="eyebrow">Local model quality</p>
            <h1>Models</h1>
          </div>
          <p className="subtitle">Tier 1-only local backtesting and model comparison.</p>
        </div>

        {latestBacktest?.warning ? <div className="state-card state-card-error">{latestBacktest.warning}</div> : null}

        <section className="detail-grid" aria-label="Active model">
          <InfoTile label="Active model" value={activeModel?.model_name ?? "No active model"} />
          <InfoTile label="Type" value={activeModel?.model_type ?? "N/A"} />
          <InfoTile label="Version" value={activeModel?.version ?? "N/A"} />
          <InfoTile label="Status" value={activeModel?.status ?? (activeModel?.is_active ? "active" : "N/A")} />
          <InfoTile label="Promoted" value={formatDate(activeModel?.promoted_at)} />
          <InfoTile label="Trained" value={formatDate(activeModel?.trained_at)} />
        </section>

        <section className="prediction-placeholder">
          <p className="panel-label">Prospective model decision</p>
          <h2>{prospectiveDecision?.decision_status === "review_required" ? "Manual model review is ready" : "Collecting unbiased final forecasts"}</h2>
          <div className="detail-grid">
            <InfoTile label="Strict finals" value={String(prospectiveDecision?.strict_final_forecasts ?? 0)} />
            <InfoTile label="Remaining" value={String(prospectiveDecision?.remaining_to_minimum ?? 100)} />
            <InfoTile label="Final capture" value={formatPercent(prospectiveDecision?.final_capture_rate)} />
            <InfoTile label="Best log loss" value={prospectiveDecision?.best_by_log_loss ?? "Pending"} />
            <InfoTile label="Best Brier" value={prospectiveDecision?.best_by_brier_score ?? "Pending"} />
            <InfoTile label="Action" value={formatAction(prospectiveDecision?.recommended_action)} />
          </div>
          {prospectiveDecision?.reasons?.length ? (
            <div className="audit-warning-list">
              {prospectiveDecision.reasons.slice(0, 4).map((reason) => (
                <p className="prediction-warning" key={reason}>{reason}</p>
              ))}
            </div>
          ) : null}
          <p>
            Candidate training: <strong>{prospectiveDecision?.candidate_training_allowed ? "review allowed" : "blocked"}</strong>. Automatic training and promotion remain disabled.
          </p>
          {prospectiveDecision?.warning ? <p className="prediction-warning">{prospectiveDecision.warning}</p> : null}
        </section>

        <section className="prediction-placeholder">
          <p className="panel-label">Candidate models</p>
          <h2>{candidates.length ? `${candidates.length} candidate models` : "No candidate models"}</h2>
          {candidates.length ? (
            <div className="sync-log-list">
              {candidates.slice(0, 6).map((model) => (
                <article className="sync-log-row" key={model.id}>
                  <div>
                    <strong>{model.model_name}</strong>
                    <span>{model.version}</span>
                  </div>
                  <span className="badge badge-upcoming">{model.status ?? "candidate"}</span>
                  <small>ID {model.id}</small>
                  <small>trained {formatDate(model.trained_at)}</small>
                  <small>promoted {formatDate(model.promoted_at)}</small>
                  <small>rejected {formatDate(model.rejected_at)}</small>
                  {model.dev_seed_warning ? <p>{model.dev_seed_warning}</p> : null}
                </article>
              ))}
            </div>
          ) : null}
          <p>
            Promote from CLI after review:{" "}
            <strong>bash scripts/promote_model.sh MODEL_VERSION_ID &quot;reason&quot;</strong>
          </p>
        </section>

        <section className="model-report-grid" aria-label="Backtest metrics">
          {["formula", "elo", "ml"].map((name) => (
            <article className="info-tile model-metric-card" key={name}>
              <span>{name.toUpperCase()}</span>
              <strong>{formatMetric(report?.models?.[name]?.log_loss)}</strong>
              <small>log loss</small>
              <dl>
                <Metric label="Accuracy" value={report?.models?.[name]?.accuracy} />
                <Metric label="Brier" value={report?.models?.[name]?.brier_score} />
                <Metric label="ROC AUC" value={report?.models?.[name]?.roc_auc} />
                <Metric label="Calibration" value={report?.models?.[name]?.calibration_error} />
              </dl>
            </article>
          ))}
        </section>

        <section className="prediction-placeholder">
          <p className="panel-label">Latest active-model backtest</p>
          <h2>{latestBacktest ? `${latestBacktest.matches_count} matches, ${latestBacktest.dataset_type}` : "No backtest found"}</h2>
          {latestBacktest ? (
            <p>
              Model <strong>{latestBacktest.model_version ?? `ID ${latestBacktest.model_version_id ?? "N/A"}`}</strong>. Status: {" "}
              <strong>{latestBacktest.model_status ?? "unknown"}</strong>.
            </p>
          ) : null}
          <p>
            Best by log loss: <strong>{report?.best_by_log_loss ?? "N/A"}</strong>. Best by Brier score:{" "}
            <strong>{report?.best_by_brier_score ?? "N/A"}</strong>.
          </p>
        </section>

        <section className="prediction-placeholder">
          <p className="panel-label">Paper tracking</p>
          <h2>{paperBets ? `${paperBets.settled_bets} settled paper tests` : "Paper tracking unavailable"}</h2>
          <div className="detail-grid">
            <InfoTile label="Pending" value={String(paperBets?.pending_bets ?? 0)} />
            <InfoTile label="Won / Lost" value={`${paperBets?.won_bets ?? 0} / ${paperBets?.lost_bets ?? 0}`} />
            <InfoTile label="Void" value={String(paperBets?.void_bets ?? 0)} />
            <InfoTile label="Profit units" value={formatMetric(paperBets?.total_profit_units)} />
            <InfoTile label="Hit rate" value={formatPercent(paperBets?.hit_rate)} />
            <InfoTile label="ROI" value={formatPercent(paperBets?.roi)} />
          </div>
          <p>Local paper tracking only. No real bets are placed by this system.</p>
          <p><strong>docker compose run --rm backend python -m app.betting.paper_bet_settlement</strong></p>
        </section>

        <section className="prediction-placeholder">
          <p className="panel-label">Prospective accuracy</p>
          <h2>{prospective?.primary_settled_forecasts ?? 0} final forecasts settled</h2>
          <div className="detail-grid">
            <InfoTile label="Final pending" value={String(prospective?.primary_pending_forecasts ?? 0)} />
            <InfoTile label="Final accuracy" value={formatMetric(prospective?.metrics.accuracy)} />
            <InfoTile label="Final log loss" value={formatMetric(prospective?.metrics.log_loss)} />
            <InfoTile label="Final Brier" value={formatMetric(prospective?.metrics.brier_score)} />
            <InfoTile label="Final capture" value={formatPercent(prospective?.coverage?.final_capture_rate)} />
            <InfoTile label="Minimum sample" value={`${prospective?.coverage?.minimum_final_forecasts ?? 100} finals`} />
            <InfoTile label="Evaluated snapshots" value={String(prospective?.settled_forecasts ?? 0)} />
            <InfoTile label="Raw snapshots" value={String(prospective?.raw_settled_forecasts ?? 0)} />
            <InfoTile label="Voided post-start" value={String(prospective?.void_forecasts ?? 0)} />
          </div>
          <div className="model-report-grid" aria-label="Forecast horizons">
            {(["early", "day_before", "final"] as const).map((horizon) => {
              const horizonReport = prospective?.by_horizon?.[horizon];
              return (
                <article className="info-tile model-metric-card" key={horizon}>
                  <span>{horizon.replace("_", " ").toUpperCase()}</span>
                  <strong>{horizonReport?.settled ?? 0}</strong>
                  <small>settled forecasts</small>
                  <dl>
                    <dt>Pending</dt>
                    <dd>{horizonReport?.pending ?? 0}</dd>
                    <Metric label="Log loss" value={horizonReport?.metrics.log_loss} />
                    <Metric label="Brier" value={horizonReport?.metrics.brier_score} />
                  </dl>
                </article>
              );
            })}
          </div>
          <div className="model-report-grid" aria-label="Prospective component metrics">
            {(["ensemble", "formula", "elo", "ml"] as const).map((component) => {
              const metrics = prospective?.all_horizons_component_metrics?.[component];
              return (
                <article className="info-tile model-metric-card" key={component}>
                  <span>{component.toUpperCase()}</span>
                  <strong>{metrics?.sample_size ?? 0}</strong>
                  <small>settled snapshots</small>
                  <dl>
                    <Metric label="Log loss" value={metrics?.log_loss} />
                    <Metric label="Brier" value={metrics?.brier_score} />
                    <Metric label="Accuracy" value={metrics?.accuracy} />
                  </dl>
                </article>
              );
            })}
          </div>
          {prospective?.by_format && Object.keys(prospective.by_format).length ? (
            <div className="model-report-grid" aria-label="Final forecast metrics by match format">
              {Object.entries(prospective.by_format).map(([format, metrics]) => (
                <article className="info-tile model-metric-card" key={format}>
                  <span>{format}</span>
                  <strong>{metrics.sample_size}</strong>
                  <small>final forecasts</small>
                  <dl>
                    <Metric label="Log loss" value={metrics.log_loss} />
                    <Metric label="Brier" value={metrics.brier_score} />
                  </dl>
                </article>
              ))}
            </div>
          ) : null}
          {prospective?.quality_gates && !prospective.quality_gates.betting_claims_allowed ? (
            <p className="prediction-warning">
              Quality gate: collecting prospective final forecasts. Accuracy claims remain disabled until the minimum sample and final-capture requirements are met.
            </p>
          ) : null}
          <div className="detail-grid" aria-label="Verified pro preview tracking">
            <InfoTile
              label="Preview final settled"
              value={String(prospective?.verified_pro_preview?.primary_settled_forecasts ?? 0)}
            />
            <InfoTile
              label="Preview pending"
              value={String(prospective?.verified_pro_preview?.pending_forecasts ?? 0)}
            />
            <InfoTile
              label="Preview all-horizon log loss"
              value={formatMetric(prospective?.verified_pro_preview?.all_horizons_metrics.log_loss)}
            />
            <InfoTile
              label="Preview all-horizon Brier"
              value={formatMetric(prospective?.verified_pro_preview?.all_horizons_metrics.brier_score)}
            />
          </div>
          <p>
            Verified-pro preview tracking is isolated from strict metrics and is never used for training or model promotion.
          </p>
          {prospective?.verified_pro_preview?.warning ? (
            <p className="prediction-warning">{prospective.verified_pro_preview.warning}</p>
          ) : null}
          {prospective?.warning ? <p className="prediction-warning">{prospective.warning}</p> : null}
          <p>Only the final snapshot, created within two hours of match start, is used as the primary accuracy metric.</p>
          <p><strong>docker compose up -d forecast-scheduler</strong></p>
        </section>

        <section className="prediction-placeholder">
          <p className="panel-label">Forecast operations</p>
          <h2>{forecastHealth?.status ? `Forecast health: ${forecastHealth.status}` : "Forecast health unavailable"}</h2>
          <div className="detail-grid">
            <InfoTile label="Upcoming eligible" value={String(forecastHealth?.summary.upcoming_prediction_eligible ?? 0)} />
            <InfoTile label="Missing current" value={String(forecastHealth?.summary.missing_current_horizon_snapshots ?? 0)} />
            <InfoTile label="Missing final" value={String(forecastHealth?.summary.missing_final_snapshots ?? 0)} />
            <InfoTile label="Historical final gaps" value={String(forecastHealth?.summary.historical_missing_final_snapshots ?? 0)} />
            <InfoTile label="Schedule drift" value={String(forecastHealth?.summary.schedule_drift_forecasts ?? 0)} />
            <InfoTile label="Settlement gaps" value={String(forecastHealth?.summary.pending_settlement_gaps ?? 0)} />
            <InfoTile label="Last refresh" value={forecastHealth?.latest_refresh?.cycle_status ?? forecastHealth?.summary.refresh_status ?? "missing"} />
            <InfoTile label="Refresh age" value={formatMinutes(forecastHealth?.summary.refresh_age_minutes)} />
            <InfoTile label="Generated" value={formatDate(forecastHealth?.generated_at)} />
          </div>
          {forecastHealth?.errors?.length ? (
            <div className="prediction-warning">
              {forecastHealth.errors.slice(0, 4).map((error) => (
                <p key={error}>{error}</p>
              ))}
            </div>
          ) : null}
          {forecastHealth?.warnings?.length ? (
            <div className="prediction-warning">
              {forecastHealth.warnings.slice(0, 4).map((warning) => (
                <p key={warning}>{warning}</p>
              ))}
            </div>
          ) : null}
          {forecastHealth?.missing_snapshots?.length ? (
            <div className="sync-log-list">
              {forecastHealth.missing_snapshots.slice(0, 5).map((item) => (
                <article className="sync-log-row" key={`${item.match_id}-${item.missing_horizon}`}>
                  <div>
                    <strong>{item.teams}</strong>
                    <span>{item.tournament ?? "Tournament unknown"}</span>
                  </div>
                  <span className="badge badge-upcoming">{item.missing_horizon}</span>
                  <small>{item.lead_time_hours.toFixed(1)}h lead</small>
                  <small>{formatDate(item.start_time)}</small>
                </article>
              ))}
            </div>
          ) : null}
          {forecastHealth?.historical_final_gaps?.length ? (
            <div className="sync-log-list">
              {forecastHealth.historical_final_gaps.slice(0, 5).map((item) => (
                <article className="sync-log-row" key={`historical-final-${item.match_id}`}>
                  <div>
                    <strong>{item.teams}</strong>
                    <span>{item.reason}</span>
                  </div>
                  <span className="badge badge-finished">missed final</span>
                  <small>{formatDate(item.start_time)}</small>
                </article>
              ))}
            </div>
          ) : null}
          {forecastHealth?.schedule_drift_gaps?.length ? (
            <div className="sync-log-list">
              {forecastHealth.schedule_drift_gaps.slice(0, 5).map((item) => (
                <article className="sync-log-row" key={`schedule-drift-${item.match_id}-${item.horizon_bucket}`}>
                  <div>
                    <strong>{item.teams}</strong>
                    <span>{item.reason}</span>
                  </div>
                  <span className="badge badge-upcoming">{item.horizon_bucket}</span>
                  <small>{item.drift_minutes.toFixed(0)} min shift</small>
                  <small>now {formatDate(item.current_scheduled_start)}</small>
                </article>
              ))}
            </div>
          ) : null}
          <p><strong>docker compose run --rm backend python -m app.prediction.forecast_gap_report</strong></p>
        </section>

        <section className="prediction-placeholder">
          <p className="panel-label">Draft experiments</p>
          <h2>
            {draftExperiments?.draft_candidates?.length
              ? `${draftExperiments.draft_candidates.length} draft candidate ${draftExperiments.draft_candidates.length === 1 ? "model" : "models"}`
              : "No draft candidates"}
          </h2>
          <p>Draft model is experimental and not used in main prediction.</p>
          {draftExperiments?.warnings?.length ? (
            <div className="prediction-warning">
              {draftExperiments.warnings.slice(0, 3).map((warning) => (
                <p key={warning}>{warning}</p>
              ))}
            </div>
          ) : null}
          {draftExperiments?.draft_candidates?.length ? (
            <div className="sync-log-list">
              {draftExperiments.draft_candidates.slice(0, 4).map((model) => {
                const metadata = model.artifact_metadata_json ?? {};
                return (
                  <article className="sync-log-row" key={model.id}>
                    <div>
                      <strong>{model.version}</strong>
                      <span>{String(metadata.selected_model ?? model.model_name)}</span>
                    </div>
                    <span className="badge badge-upcoming">experimental</span>
                    <small>feature {String(metadata.feature_version ?? "draft_v1")}</small>
                    <small>rows {String(metadata.rows_count ?? "N/A")}</small>
                    <small>status {model.status ?? "candidate"}</small>
                  </article>
                );
              })}
            </div>
          ) : null}
          <div className="model-report-grid" aria-label="Draft backtest metrics">
            {["formula", "elo", "prematch_ml", "ensemble", "draft_model"].map((name) => (
              <article className="info-tile model-metric-card" key={name}>
                <span>{name.replace("_", " ").toUpperCase()}</span>
                <strong>{formatMetric(draftExperiments?.latest_draft_backtest?.metrics?.[name]?.log_loss)}</strong>
                <small>log loss</small>
                <dl>
                  <Metric label="Accuracy" value={draftExperiments?.latest_draft_backtest?.metrics?.[name]?.accuracy} />
                  <Metric label="Brier" value={draftExperiments?.latest_draft_backtest?.metrics?.[name]?.brier_score} />
                </dl>
              </article>
            ))}
          </div>
          <p>
            Best by log loss: <strong>{draftExperiments?.latest_draft_backtest?.best_by_log_loss ?? "N/A"}</strong>. Best
            by Brier score: <strong>{draftExperiments?.latest_draft_backtest?.best_by_brier_score ?? "N/A"}</strong>.
          </p>
        </section>
      </section>
    </main>
  );
}

type QualityReport = {
  models?: Record<string, Record<string, number | null>>;
  best_by_log_loss?: string | null;
  best_by_brier_score?: string | null;
};

async function fetchOptional<T>(path: string): Promise<T | null> {
  try {
    return await fetchFromBackend<T>(path);
  } catch {
    return null;
  }
}

function InfoTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="info-tile">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number | null | undefined }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{formatMetric(value)}</dd>
    </>
  );
}

function formatMetric(value: number | null | undefined): string {
  return typeof value === "number" ? value.toFixed(3) : "N/A";
}

function formatPercent(value: number | null | undefined): string {
  return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "N/A";
}

function formatMinutes(value: number | null | undefined): string {
  return typeof value === "number" ? `${Math.round(value)} min` : "N/A";
}

function formatAction(value: string | null | undefined): string {
  if (!value) {
    return "Pending";
  }
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatDate(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString() : "N/A";
}
