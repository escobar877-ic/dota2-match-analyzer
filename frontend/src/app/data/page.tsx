import {
  DataCoverageReport,
  DataSourcesStatusResponse,
  MatchDetailEnrichmentReport,
  MatchValidationReport,
  ProjectAuditReport,
  ImportQualityReport,
  RealBatchReport,
  SourceHealthReport,
  StratzMatchIdImportReport,
  StratzMatchIdValidationReport,
  UpcomingSyncReport,
  HistoricalFetchPlan,
  SyncReviewReport,
  SourceMappingStatus,
  RealIngestionPlan,
  SyncLog,
  fetchFromBackend,
} from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function DataPage() {
  const [
    status,
    logs,
    coverage,
    audit,
    matchValidation,
    ingestionPlan,
    importQuality,
    realBatchReport,
    sourceHealth,
    fetchPlan,
    syncReview,
    sourceMappings,
    stratzValidation,
    stratzImport,
    upcomingSync,
    matchDetailEnrichment,
  ] = await Promise.all([
    fetchOptional<DataSourcesStatusResponse>("/data-sources/status"),
    fetchOptional<SyncLog[]>("/sync/logs?limit=12"),
    fetchOptional<DataCoverageReport>("/data/coverage"),
    fetchOptional<ProjectAuditReport>("/data/audit"),
    fetchOptional<MatchValidationReport>("/data/match-validation"),
    fetchOptional<RealIngestionPlan>("/data/real-ingestion-plan"),
    fetchOptional<ImportQualityReport>("/data/import-quality"),
    fetchOptional<RealBatchReport>("/data/real-batch-report"),
    fetchOptional<SourceHealthReport>("/data/source-health"),
    fetchOptional<HistoricalFetchPlan>("/data/historical-fetch-plan"),
    fetchOptional<SyncReviewReport>("/data/sync-review"),
    fetchOptional<SourceMappingStatus>("/data/source-mappings/status"),
    fetchOptional<StratzMatchIdValidationReport>("/data/stratz-match-id-validation"),
    fetchOptional<StratzMatchIdImportReport>("/data/stratz-match-id-import"),
    fetchOptional<UpcomingSyncReport>("/data/upcoming-sync-report"),
    fetchOptional<MatchDetailEnrichmentReport>("/data/match-detail-enrichment"),
  ]);
  const sources = status?.sources ?? {};
  const realHistoricalCount = coverage?.real_tier1_historical_matches_count
    ?? ingestionPlan?.coverage?.real_tier1_historical_matches_count
    ?? 0;
  const totalTier1Historical = coverage?.tier1_historical_matches_count
    ?? ingestionPlan?.coverage?.tier1_historical_matches_count
    ?? 0;
  const importQualityCurrent = hasGeneratedAt(importQuality);
  const realBatchReportCurrent = hasGeneratedAt(realBatchReport);
  const stratzImportCurrent = reportsAreConsistent(stratzValidation, stratzImport);

  return (
    <main className="page-main">
      <section className="home-shell" aria-label="Data sync status">
        <div className="page-header">
          <div>
            <p className="eyebrow">Real data sync</p>
            <h1>Data</h1>
          </div>
          <p className="subtitle">Read-only source status and Tier 1 sync logs.</p>
        </div>

        <section className="prediction-placeholder real-data-setup">
          <p className="panel-label">Real data setup</p>
          <h2>Missing API keys do not break local mode.</h2>
          <p>
            Enabled sources: <strong>{enabledSources(sources)}</strong>. Missing keys:{" "}
            <strong>{missingRequiredKeys(sources)}</strong>.
          </p>
          <p>Setup guide: docs/REAL_DATA_SETUP.md</p>
        </section>

        <section className="prediction-placeholder data-coverage-panel">
          <p className="panel-label">Real ingestion plan</p>
          <h2>{ingestionPlan ? auditStatusLabel(ingestionPlan.status) : "Plan unavailable"}</h2>
          {ingestionPlan?.status === "missing" ? (
            <p>{ingestionPlan.message ?? "Run python -m worker.data_ingestion.real_ingestion_plan"}</p>
          ) : ingestionPlan ? (
            <>
              <ReportMeta generatedAt={ingestionPlan.generated_at} maxAgeHours={12} />
              <div className="data-source-grid coverage-grid">
                <article className="info-tile">
                  <span>Real historical</span>
                  <strong>{realHistoricalCount}</strong>
                  <small>{totalTier1Historical} total Tier 1 historical</small>
                </article>
                <article className="info-tile">
                  <span>Usable progress</span>
                  <strong>{Math.min(realHistoricalCount, 300)}/300</strong>
                  <small>{Math.max(0, 300 - realHistoricalCount)} remaining</small>
                </article>
                <article className="info-tile">
                  <span>Good progress</span>
                  <strong>{Math.min(realHistoricalCount, 1000)}/1000</strong>
                  <small>{Math.max(0, 1000 - realHistoricalCount)} remaining</small>
                </article>
                <article className="info-tile">
                  <span>Sources</span>
                  <strong>{ingestionPlan.available_sources?.join(", ") || "none"}</strong>
                  <small>missing {ingestionPlan.missing_keys?.join(", ") || "none"}</small>
                </article>
              </div>
              {ingestionPlan.blockers?.length ? (
                <div className="audit-warning-list">
                  {ingestionPlan.blockers.slice(0, 4).map((warning) => (
                    <p className="prediction-warning" key={warning}>{warning}</p>
                  ))}
                </div>
              ) : null}
              <p>Next: {ingestionPlan.recommended_commands?.[0] ?? "bash scripts/real_ingestion_plan.sh"}</p>
            </>
          ) : (
            <p>Run scripts/real_ingestion_plan.sh to generate ml/artifacts/real_ingestion_plan.json.</p>
          )}
        </section>

        <section className="prediction-placeholder data-coverage-panel">
          <p className="panel-label">Import quality</p>
          <h2>{importQualityCurrent ? auditStatusLabel(importQuality!.status) : "Import quality snapshot unavailable"}</h2>
          {importQuality?.status === "missing" ? (
            <p>{importQuality.message ?? "Run import quality report before applying CSV."}</p>
          ) : importQualityCurrent && importQuality ? (
            <>
              <ReportMeta generatedAt={importQuality.generated_at} maxAgeHours={168} />
              <div className="data-source-grid coverage-grid">
                <article className="info-tile">
                  <span>Rows seen</span>
                  <strong>{importQuality.rows_seen ?? 0}</strong>
                  <small>{importQuality.file ?? "latest report"}</small>
                </article>
                <article className="info-tile">
                  <span>Estimated valid</span>
                  <strong>{importQuality.estimated_valid_rows ?? 0}</strong>
                  <small>before apply</small>
                </article>
                <article className="info-tile">
                  <span>Estimated excluded</span>
                  <strong>{importQuality.estimated_excluded_rows ?? 0}</strong>
                  <small>quality exclusions</small>
                </article>
                <article className="info-tile">
                  <span>Errors</span>
                  <strong>{importQuality.errors?.length ?? 0}</strong>
                  <small>{importQuality.warnings?.length ?? 0} warnings</small>
                </article>
              </div>
              <p>Regenerate: bash scripts/check_import_quality.sh imports/tier1_matches_template.csv</p>
            </>
          ) : (
            <p className="prediction-warning">Legacy or missing report ignored. Run import quality again before applying CSV.</p>
          )}
        </section>

        <section className="prediction-placeholder data-coverage-panel">
          <p className="panel-label">Real batch pipeline</p>
          <h2>{realBatchReportCurrent ? auditStatusLabel(realBatchReport!.status) : "Real batch snapshot unavailable"}</h2>
          {realBatchReport?.status === "missing" ? (
            <p>{realBatchReport.message ?? "Run scripts/real_batch_pipeline.sh <csv>"}</p>
          ) : realBatchReportCurrent && realBatchReport ? (
            <>
              <ReportMeta generatedAt={realBatchReport.generated_at} maxAgeHours={168} snapshotLabel="Batch snapshot" />
              <div className="data-source-grid coverage-grid">
                <article className="info-tile">
                  <span>Real matches</span>
                  <strong>{realBatchReport.real_matches_after ?? 0}</strong>
                  <small>before {realBatchReport.real_matches_before ?? "N/A"}</small>
                </article>
                <article className="info-tile">
                  <span>Imported / excluded</span>
                  <strong>{realBatchReport.imported_rows ?? 0}</strong>
                  <small>{realBatchReport.would_import_rows ?? 0} pending / {realBatchReport.excluded_rows ?? 0} excluded</small>
                </article>
                <article className="info-tile">
                  <span>Readiness</span>
                  <strong>{realBatchReport.coverage_readiness ?? "unknown"}</strong>
                  <small>{realBatchReport.dev_seed_only ? "dev seed only" : realBatchReport.dataset_type ?? "unknown"}</small>
                </article>
                <article className="info-tile">
                  <span>Candidate</span>
                  <strong>{realBatchReport.candidate_created ? "Created" : "None"}</strong>
                  <small>{realBatchReport.candidate_version ?? "no candidate"}</small>
                </article>
              </div>
              {realBatchReport.warnings?.length ? (
                <div className="audit-warning-list">
                  {realBatchReport.warnings.slice(0, 4).map((warning) => (
                    <p className="prediction-warning" key={warning}>{warning}</p>
                  ))}
                </div>
              ) : null}
              <p>{realBatchReport.recommended_next_step ?? "Validate and dry-run a real CSV batch first."}</p>
            </>
          ) : (
            <p className="prediction-warning">Legacy or missing batch snapshot ignored. Run scripts/real_batch_pipeline.sh imports/real_batches/file.csv.</p>
          )}
        </section>

        <section className="prediction-placeholder data-coverage-panel">
          <p className="panel-label">Source health</p>
          <h2>{sourceHealth ? auditStatusLabel(sourceHealth.status) : "Source health unavailable"}</h2>
          {sourceHealth?.status === "missing" ? <p>{sourceHealth.message}</p> : null}
          {sourceHealth?.sources ? (
            <div className="sync-log-list">
              {Object.entries(sourceHealth.sources).map(([source, item]) => (
                <article className="sync-log-row" key={source}>
                  <div>
                    <strong>{source}</strong>
                    <span>{item.enabled ? "enabled" : "disabled"}</span>
                  </div>
                  <small>key {item.has_api_key ? "configured" : "missing"}</small>
                  <small>connect {item.can_connect === null || item.can_connect === undefined ? "not checked" : item.can_connect ? "ok" : "no"}</small>
                  {item.last_error ? <small>{item.last_error}</small> : null}
                </article>
              ))}
            </div>
          ) : null}
        </section>

        <section className="prediction-placeholder data-coverage-panel">
          <p className="panel-label">Historical fetch plan</p>
          <h2>{fetchPlan ? auditStatusLabel(fetchPlan.status) : "Fetch plan unavailable"}</h2>
          {fetchPlan?.status === "missing" ? <p>{fetchPlan.message}</p> : null}
          {fetchPlan?.recommended_windows?.length ? (
            <div className="sync-log-list">
              {fetchPlan.recommended_windows.map((window) => (
                <article className="sync-log-row" key={window.label}>
                  <div>
                    <strong>{window.label}</strong>
                    <span>{window.start_date} to {window.end_date}</span>
                  </div>
                  <small>{fetchPlan.available_sources?.join(", ") || "no sources"}</small>
                </article>
              ))}
            </div>
          ) : null}
          {fetchPlan?.blockers?.slice(0, 3).map((warning) => (
            <p className="prediction-warning" key={warning}>{warning}</p>
          ))}
          <p>{fetchPlan?.command_hints?.[0] ?? "Run bash scripts/historical_fetch_plan.sh"}</p>
        </section>

        <section className="prediction-placeholder data-coverage-panel">
          <p className="panel-label">Sync review</p>
          <h2>{syncReview ? auditStatusLabel(syncReview.status) : "Sync review unavailable"}</h2>
          {syncReview?.status === "missing" ? <p>{syncReview.message}</p> : null}
          {syncReview && syncReview.status !== "missing" ? (
            <>
              <div className="data-source-grid coverage-grid">
                <article className="info-tile">
                  <span>Records seen</span>
                  <strong>{syncReview.records_seen ?? 0}</strong>
                  <small>{syncReview.source ?? "latest source"}</small>
                </article>
                <article className="info-tile">
                  <span>Valid rows</span>
                  <strong>{syncReview.valid_rows ?? 0}</strong>
                  <small>create {(syncReview.would_create ?? 0)} / update {(syncReview.would_update ?? 0)}</small>
                </article>
                <article className="info-tile">
                  <span>Excluded</span>
                  <strong>{syncReview.would_exclude ?? 0}</strong>
                  <small>{Object.keys(syncReview.top_exclusion_reasons ?? {}).length} reason types</small>
                </article>
                <article className="info-tile">
                  <span>Unknown mappings</span>
                  <strong>{(syncReview.unknown_teams?.length ?? 0) + (syncReview.unknown_tournaments?.length ?? 0)}</strong>
                  <small>teams and tournaments</small>
                </article>
                <article className="info-tile">
                  <span>Apply</span>
                  <strong>{syncReview.apply_allowed ? "Allowed" : "Blocked"}</strong>
                  <small>{syncReview.source_trust_level ?? "unknown trust"}</small>
                </article>
              </div>
              <div className="sync-log-list">
                {Object.entries(syncReview.top_exclusion_reasons ?? {}).slice(0, 5).map(([reason, count]) => (
                  <article className="sync-log-row" key={reason}>
                    <div>
                      <strong>{reason}</strong>
                      <span>{count} rows</span>
                    </div>
                  </article>
                ))}
              </div>
              {syncReview.unknown_teams?.length ? <p>Unknown teams: {syncReview.unknown_teams.slice(0, 8).join(", ")}</p> : null}
              {syncReview.unknown_tournaments?.length ? <p>Unknown tournaments: {syncReview.unknown_tournaments.slice(0, 8).join(", ")}</p> : null}
              {syncReview.alias_suggestions?.teams || syncReview.alias_suggestions?.tournaments ? (
                <p>
                  Alias suggestions:{" "}
                  {formatAliasSuggestions(syncReview.alias_suggestions)}
                </p>
              ) : null}
              {syncReview.blocked_alias_suggestions?.length ? (
                <p className="prediction-warning">
                  {syncReview.blocked_alias_suggestions.length} alias suggestions blocked by safety checks.
                </p>
              ) : null}
              {syncReview.apply_block_reason ? <p className="prediction-warning">{syncReview.apply_block_reason}</p> : null}
              <p>Next: {syncReview.recommended_action ?? "Run sync review after a historical sync dry-run."}</p>
            </>
          ) : null}
        </section>

        <section className="prediction-placeholder data-coverage-panel">
          <p className="panel-label">PandaScore upcoming sync</p>
          <h2>{upcomingSync ? auditStatusLabel(upcomingSync.status) : "Upcoming sync unavailable"}</h2>
          {upcomingSync?.status === "missing" ? <p>{upcomingSync.message}</p> : null}
          <div className="data-source-grid coverage-grid">
            <article className="info-tile">
              <span>Records seen</span>
              <strong>{upcomingSync?.records_seen ?? 0}</strong>
              <small>{upcomingSync?.source ?? "pandascore"}</small>
            </article>
            <article className="info-tile">
              <span>Prediction eligible</span>
              <strong>{upcomingSync?.prediction_eligible_count ?? 0}</strong>
              <small>{upcomingSync?.prediction_blocked_count ?? 0} blocked</small>
            </article>
            <article className="info-tile">
              <span>Saved candidates</span>
              <strong>{upcomingSync?.saved_upcoming_candidates ?? 0}</strong>
              <small>{upcomingSync?.truly_invalid_count ?? 0} invalid</small>
            </article>
            <article className="info-tile">
              <span>Competition tiers</span>
              <strong>{upcomingSync?.tier1_upcoming_count ?? 0} Tier 1</strong>
              <small>{upcomingSync?.pro_upcoming_count ?? 0} pro / {upcomingSync?.qualifier_upcoming_count ?? 0} qualifier</small>
            </article>
          </div>
          {upcomingSync?.source_errors?.slice(0, 3).map((error) => (
            <p className="prediction-warning" key={error}>{error}</p>
          ))}
          {upcomingSync?.warnings?.slice(0, 3).map((warning) => (
            <p className="prediction-warning" key={warning}>{warning}</p>
          ))}
          <p>Dry-run: bash scripts/sync_upcoming_matches.sh --source pandascore --limit 50</p>
        </section>

        <section className="prediction-placeholder data-coverage-panel">
          <p className="panel-label">Historical match enrichment</p>
          <h2>
            {matchDetailEnrichment
              ? auditStatusLabel(matchDetailEnrichment.status)
              : "Enrichment report unavailable"}
          </h2>
          {matchDetailEnrichment?.status === "missing" ? <p>{matchDetailEnrichment.message}</p> : null}
          <div className="data-source-grid coverage-grid">
            <article className="info-tile">
              <span>Matches enriched</span>
              <strong>{matchDetailEnrichment?.total_enriched_matches ?? matchDetailEnrichment?.matches_enriched ?? 0}</strong>
              <small>{matchDetailEnrichment?.matches_enriched ?? 0} in latest batch</small>
            </article>
            <article className="info-tile">
              <span>Team stats</span>
              <strong>{matchDetailEnrichment?.total_stats_rows ?? matchDetailEnrichment?.stats_rows_created ?? 0}</strong>
              <small>{matchDetailEnrichment?.stats_rows_created ?? 0} created in latest batch</small>
            </article>
            <article className="info-tile">
              <span>Draft entries</span>
              <strong>{matchDetailEnrichment?.total_draft_entries ?? matchDetailEnrichment?.draft_entries_created ?? 0}</strong>
              <small>{matchDetailEnrichment?.total_draft_snapshots ?? matchDetailEnrichment?.draft_snapshots_created ?? 0} total snapshots</small>
            </article>
            <article className="info-tile">
              <span>Mode</span>
              <strong>{matchDetailEnrichment?.mode ?? "none"}</strong>
              <small>
                {matchDetailEnrichment?.records_excluded ?? 0} excluded / {matchDetailEnrichment?.rate_limit_retries_used ?? 0} retries
              </small>
            </article>
          </div>
          {matchDetailEnrichment?.source_errors?.slice(0, 3).map((error) => (
            <p className="prediction-warning" key={error}>{error}</p>
          ))}
          {matchDetailEnrichment?.warnings?.slice(0, 3).map((warning) => (
            <p className="prediction-warning" key={warning}>{warning}</p>
          ))}
          <p>Dry-run: bash scripts/enrich_match_details.sh --limit 50</p>
          <p>Apply after review: bash scripts/enrich_match_details.sh --limit 50 --apply</p>
          <p className="muted-copy">This enrichment does not train or promote a model.</p>
        </section>

        <section className="prediction-placeholder data-coverage-panel">
          <p className="panel-label">STRATZ match ID import</p>
          <h2>{stratzValidation ? auditStatusLabel(stratzValidation.status) : "STRATZ validation unavailable"}</h2>
          <p className="prediction-warning">Use only manually verified Tier 1 match IDs. Date-range STRATZ sync is not used for apply.</p>
          {stratzValidation?.status === "missing" ? <p>{stratzValidation.message}</p> : null}
          {stratzValidation?.generated_at ? <ReportMeta generatedAt={stratzValidation.generated_at} maxAgeHours={168} snapshotLabel="Validation" /> : null}
          {!stratzImportCurrent && stratzImport ? (
            <p className="prediction-warning">The import report is older than or belongs to a different validation batch. Its apply status is ignored.</p>
          ) : null}
          <div className="data-source-grid coverage-grid">
            <article className="info-tile">
              <span>Validation rows</span>
              <strong>{stratzValidation?.rows_seen ?? 0}</strong>
              <small>{stratzValidation?.tier1_valid_count ?? 0} Tier 1 valid</small>
            </article>
            <article className="info-tile">
              <span>Valid / invalid IDs</span>
              <strong>{stratzValidation?.valid_match_ids?.length ?? 0}</strong>
              <small>{stratzValidation?.invalid_match_ids?.length ?? 0} invalid</small>
            </article>
            <article className="info-tile">
              <span>Safe to apply</span>
              <strong>{stratzValidation?.safe_to_apply ? "Yes" : "No"}</strong>
              <small>{stratzValidation?.mismatched_expected_fields?.length ?? 0} mismatches</small>
            </article>
            <article className="info-tile">
              <span>Import dry-run</span>
              <strong>{stratzImportCurrent ? stratzImport?.mode ?? "none" : "stale"}</strong>
              <small>
                create {stratzImportCurrent ? stratzImport?.would_create ?? 0 : 0} / update {stratzImportCurrent ? stratzImport?.would_update ?? 0 : 0}
              </small>
            </article>
          </div>
          {stratzImportCurrent && stratzImport?.apply_block_reason ? <p className="prediction-warning">{stratzImport.apply_block_reason}</p> : null}
          {stratzValidation?.errors?.slice(0, 3).map((error) => (
            <p className="prediction-warning" key={error}>{error}</p>
          ))}
          {stratzImportCurrent && stratzImport?.source_errors?.slice(0, 3).map((error) => (
            <p className="prediction-warning" key={error}>{error}</p>
          ))}
          <p>Validate: bash scripts/validate_stratz_match_ids.sh imports/stratz_batches/tier1_stratz_batch_001.csv</p>
          <p>Dry-run: bash scripts/import_stratz_match_ids.sh imports/stratz_batches/tier1_stratz_batch_001.csv</p>
        </section>

        <section className="prediction-placeholder data-coverage-panel">
          <p className="panel-label">Source mappings</p>
          <h2>{sourceMappings ? auditStatusLabel(sourceMappings.status) : "Source mappings unavailable"}</h2>
          {sourceMappings ? (
            <>
              <div className="data-source-grid coverage-grid">
                <article className="info-tile">
                  <span>Mapped teams</span>
                  <strong>{sourceMappings.mapped_teams_count ?? 0}</strong>
                  <small>manual mappings</small>
                </article>
                <article className="info-tile">
                  <span>Mapped tournaments</span>
                  <strong>{sourceMappings.mapped_tournaments_count ?? 0}</strong>
                  <small>manual mappings</small>
                </article>
                <article className="info-tile">
                  <span>Invalid mappings</span>
                  <strong>{sourceMappings.invalid_mappings_count ?? 0}</strong>
                  <small>must map to Tier 1 canonical names</small>
                </article>
                <article className="info-tile">
                  <span>Config</span>
                  <strong>{sourceMappings.mapping_path ?? "config/source_mappings.json"}</strong>
                  <small>unknowns are not auto-added</small>
                </article>
              </div>
              {sourceMappings.invalid_mappings?.slice(0, 5).map((mapping) => (
                <p className="prediction-warning" key={`${mapping.source}-${mapping.kind}-${mapping.key}`}>
                  {mapping.source} {mapping.kind} {mapping.key} maps to non-Tier 1 name {mapping.canonical_name}.
                </p>
              ))}
              {sourceMappings.message ? <p>{sourceMappings.message}</p> : null}
            </>
          ) : (
            <p>Check config/source_mappings.json before applying real source rows.</p>
          )}
        </section>

        <section className="prediction-placeholder data-coverage-panel">
          <p className="panel-label">Match validation</p>
          <h2>{matchValidation ? auditStatusLabel(matchValidation.status) : "Validation unavailable"}</h2>
          {matchValidation?.status === "missing" ? (
            <p>{matchValidation.message ?? "Run python -m worker.data_ingestion.match_validation"}</p>
          ) : matchValidation ? (
            <>
              <div className="data-source-grid coverage-grid">
                <article className="info-tile">
                  <span>Errors</span>
                  <strong>{matchValidation.errors?.length ?? 0}</strong>
                  <small>validation failures</small>
                </article>
                <article className="info-tile">
                  <span>Warnings</span>
                  <strong>{matchValidation.warnings?.length ?? 0}</strong>
                  <small>review recommended</small>
                </article>
                <article className="info-tile">
                  <span>Suspects</span>
                  <strong>{matchValidation.suspect_matches?.length ?? 0}</strong>
                  <small>{matchValidation.summary?.suspect_matches_count ?? 0} total flagged</small>
                </article>
                <article className="info-tile">
                  <span>Total matches</span>
                  <strong>{matchValidation.summary?.total_matches ?? 0}</strong>
                  <small>{matchValidation.summary?.tier1_matches ?? 0} Tier 1</small>
                </article>
              </div>
              {matchValidation.source_summary ? (
                <div className="sync-log-list">
                  {Object.entries(matchValidation.source_summary).map(([source, sourceSummary]) => (
                    <article className="sync-log-row" key={source}>
                      <div>
                        <strong>{source}</strong>
                        <span>{sourceSummary.total_matches} matches</span>
                      </div>
                      <small>valid {sourceSummary.valid_matches}</small>
                      <small>invalid {sourceSummary.invalid_matches}</small>
                      <small>excluded {sourceSummary.excluded_matches}</small>
                      <small>dupes {sourceSummary.duplicate_warnings}</small>
                    </article>
                  ))}
                </div>
              ) : null}
              {matchValidation.suspect_matches?.length ? (
                <div className="sync-log-list">
                  {matchValidation.suspect_matches.slice(0, 10).map((suspect) => (
                    <article className="sync-log-row" key={`${suspect.match_id}-${suspect.reason}`}>
                      <div>
                        <strong>Match {suspect.match_id}</strong>
                        <span>{suspect.external_source ?? "unknown"}</span>
                      </div>
                      <small>{suspect.teams.filter(Boolean).join(" vs ")}</small>
                      <small>{suspect.tournament ?? "No tournament"}</small>
                      <small>{suspect.reason}</small>
                    </article>
                  ))}
                </div>
              ) : null}
              <p>Regenerate: scripts/match_validation.sh</p>
            </>
          ) : (
            <p>Run scripts/match_validation.sh to generate ml/artifacts/match_validation_report.json.</p>
          )}
        </section>

        <section className="prediction-placeholder data-coverage-panel">
          <p className="panel-label">Project audit</p>
          <h2>{audit ? auditStatusLabel(audit.status) : "Audit unavailable"}</h2>
          {audit?.status === "missing" ? (
            <p>{audit.message ?? "Run python -m worker.data_ingestion.project_audit"}</p>
          ) : audit ? (
            <>
              <div className="data-source-grid coverage-grid">
                <article className="info-tile">
                  <span>Errors</span>
                  <strong>{audit.errors?.length ?? 0}</strong>
                  <small>{audit.checks ? Object.values(audit.checks).filter((value) => value === "failed").length : 0} failed checks</small>
                </article>
                <article className="info-tile">
                  <span>Warnings</span>
                  <strong>{audit.warnings?.length ?? 0}</strong>
                  <small>{audit.checks ? Object.values(audit.checks).filter((value) => value === "warning").length : 0} warning checks</small>
                </article>
                <article className="info-tile">
                  <span>Tier 1 matches</span>
                  <strong>{audit.summary?.tier1_matches ?? 0}</strong>
                  <small>{audit.summary?.finished_tier1_matches ?? 0} finished</small>
                </article>
                <article className="info-tile">
                  <span>Model artifacts</span>
                  <strong>{audit.summary?.model?.artifacts_readable ? "Readable" : "Check"}</strong>
                  <small>active {audit.summary?.model?.active_model_id ?? "none"}</small>
                </article>
              </div>
              <p>
                Last generated:{" "}
                <strong>{audit.summary?.generated_at ? formatDate(audit.summary.generated_at) : "not generated"}</strong>.
              </p>
              {audit.warnings?.length ? (
                <div className="audit-warning-list">
                  {audit.warnings.slice(0, 4).map((warning) => (
                    <p className="prediction-warning" key={warning}>
                      {warning}
                    </p>
                  ))}
                </div>
              ) : null}
              <p>Regenerate: scripts/project_audit.sh</p>
            </>
          ) : (
            <p>Run scripts/project_audit.sh to generate ml/artifacts/project_audit_report.json.</p>
          )}
        </section>

        <section className="prediction-placeholder data-coverage-panel">
          <p className="panel-label">Historical data coverage</p>
          <h2>{coverage ? readinessLabel(coverage.training_readiness) : "Coverage unavailable"}</h2>
          {coverage ? (
            <>
              <div className="data-source-grid coverage-grid">
                <article className="info-tile">
                  <span>Real Tier 1</span>
                  <strong>{coverage.real_tier1_historical_matches_count ?? coverage.tier1_historical_matches_count}</strong>
                  <small>{coverage.tier1_teams_count} known Tier 1 teams</small>
                </article>
                <article className="info-tile">
                  <span>Verified pro</span>
                  <strong>{coverage.verified_pro_historical_matches_count ?? 0}</strong>
                  <small>Training weight 0.5</small>
                </article>
                <article className="info-tile">
                  <span>Hybrid eligible</span>
                  <strong>{coverage.real_training_eligible_matches_count ?? 0}</strong>
                  <small>Real rows available for candidate training</small>
                </article>
                <article className="info-tile">
                  <span>Dev seed</span>
                  <strong>{coverage.dev_seed_historical_matches_count ?? 0}</strong>
                  <small>Excluded from real training</small>
                </article>
                <article className="info-tile">
                  <span>With winner</span>
                  <strong>{coverage.matches_with_winner_count}</strong>
                  <small>Finished matches usable for labels</small>
                </article>
                <article className="info-tile">
                  <span>Patch coverage</span>
                  <strong>{formatPercent(coverage.patch_coverage_ratio)}</strong>
                  <small>{coverage.matches_with_patch_context_count} matches with patch context</small>
                </article>
                <article className="info-tile">
                  <span>Roster coverage</span>
                  <strong>{formatPercent(coverage.roster_coverage_ratio)}</strong>
                  <small>{coverage.matches_with_roster_context_count} matches with complete 5v5 roster context</small>
                </article>
              </div>
              <p>
                Date range: <strong>{coverage.date_range.from ? formatDate(coverage.date_range.from) : "none"}</strong>{" "}
                to <strong>{coverage.date_range.to ? formatDate(coverage.date_range.to) : "none"}</strong>.
              </p>
              {coverage.warning || coverage.dev_seed_only ? (
                <p className="prediction-warning">
                  {coverage.warning ?? "Coverage is synthetic dev seed only and is not real accuracy."}
                </p>
              ) : null}
            </>
          ) : (
            <p>Run scripts/data_coverage.sh to generate ml/artifacts/data_coverage_report.json.</p>
          )}
        </section>

        <section className="data-source-grid" aria-label="Data source status">
          {Object.entries(sources).map(([name, source]) => (
            <article className="info-tile data-source-card" key={name}>
              <span>{name.toUpperCase()}</span>
              <strong>{source.enabled ? "Enabled" : "Disabled"}</strong>
              <small>API key {source.has_api_key ? "configured" : "missing"}</small>
              <small>Last sync: {source.last_sync_status}</small>
              {source.last_error ? <p>{source.last_error}</p> : null}
              {source.setup_hint ? <small>{source.setup_hint}</small> : null}
            </article>
          ))}
        </section>

        <section className="prediction-placeholder data-log-panel">
          <p className="panel-label">Last sync logs</p>
          <h2>{logs?.length ? `${logs.length} recent runs` : "No sync logs found"}</h2>
          {logs?.length ? (
            <div className="sync-log-list">
              {logs.map((log) => (
                <article className="sync-log-row" key={log.id}>
                  <div>
                    <strong>{log.source}</strong>
                    <span>{log.sync_type}</span>
                  </div>
                  <span className={`badge ${log.status === "ok" ? "badge-muted" : "badge-upcoming"}`}>{log.status}</span>
                  <small>seen {log.records_seen}</small>
                  <small>created {log.records_created}</small>
                  <small>updated {log.records_updated}</small>
                  <small>excluded {log.records_excluded}</small>
                  <small>{formatDate(log.started_at)}</small>
                  {log.error_message ? <p>{log.error_message}</p> : null}
                </article>
              ))}
            </div>
          ) : null}
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

function hasGeneratedAt(report: { generated_at?: string } | null): boolean {
  return Boolean(report?.generated_at && Number.isFinite(new Date(report.generated_at).getTime()));
}

function reportsAreConsistent(
  validation: StratzMatchIdValidationReport | null,
  importReport: StratzMatchIdImportReport | null,
): boolean {
  if (!hasGeneratedAt(validation) || !hasGeneratedAt(importReport)) {
    return false;
  }
  if (validation?.file && importReport?.file && validation.file !== importReport.file) {
    return false;
  }
  return new Date(importReport!.generated_at!).getTime() >= new Date(validation!.generated_at!).getTime();
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}

function enabledSources(sources: DataSourcesStatusResponse["sources"]): string {
  const names = Object.entries(sources)
    .filter(([, source]) => source.enabled)
    .map(([name]) => name);
  return names.length ? names.join(", ") : "none";
}

function missingRequiredKeys(sources: DataSourcesStatusResponse["sources"]): string {
  const names = Object.entries(sources)
    .filter(([, source]) => !source.has_api_key && source.capabilities?.requires_api_key === true)
    .map(([name]) => name);
  return names.length ? names.join(", ") : "none";
}

function ReportMeta({
  generatedAt,
  maxAgeHours,
  snapshotLabel = "Generated",
}: {
  generatedAt?: string;
  maxAgeHours: number;
  snapshotLabel?: string;
}) {
  if (!generatedAt) {
    return <p className="prediction-warning">Legacy snapshot without generation time. Regenerate it before acting on these values.</p>;
  }
  const ageMs = Date.now() - new Date(generatedAt).getTime();
  const stale = !Number.isFinite(ageMs) || ageMs > maxAgeHours * 60 * 60 * 1000;
  return (
    <p className={stale ? "prediction-warning" : "confidence-line"}>
      {snapshotLabel}: <strong>{formatDate(generatedAt)}</strong>{stale ? ". This report is stale; regenerate it before use." : "."}
    </p>
  );
}

function readinessLabel(value: string): string {
  if (value === "good") {
    return "Good training coverage";
  }
  if (value === "usable") {
    return "Usable training coverage";
  }
  return "Insufficient training coverage";
}

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function auditStatusLabel(value: string): string {
  if (value === "ok") {
    return "Audit OK";
  }
  if (value === "failed") {
    return "Audit failed";
  }
  if (value === "missing") {
    return "Audit report missing";
  }
  return "Audit warning";
}

function formatAliasSuggestions(suggestions: SyncReviewReport["alias_suggestions"]): string {
  const entries = [
    ...Object.entries(suggestions?.teams ?? {}),
    ...Object.entries(suggestions?.tournaments ?? {}),
  ];
  if (!entries.length) {
    return "none";
  }
  return entries
    .slice(0, 4)
    .map(([raw, values]) => `${raw} -> ${values.slice(0, 2).map((value) => `${value.suggested_canonical} (${value.risk})`).join(" / ")}`)
    .join("; ");
}
