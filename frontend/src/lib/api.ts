export type Team = {
  id: number;
  external_source: string | null;
  external_id: string | null;
  name: string;
  logo_url: string | null;
  country: string | null;
  region: string | null;
  tier: string | null;
  is_active_tier1: boolean;
  excluded_reason: string | null;
  created_at: string;
  updated_at: string;
};

export type TeamMatchStats = {
  id: number;
  match_id: number;
  team_id: number;
  side: string | null;
  kills: number | null;
  deaths: number | null;
  assists: number | null;
  gold_diff_10: number | null;
  xp_diff_10: number | null;
  duration: number | null;
  result: string | null;
  created_at: string;
};

export type Prediction = {
  id: number;
  match_id: number;
  team_a_probability: number;
  team_b_probability: number;
  confidence: number;
  explanation_json: Record<string, unknown> | null;
  model_type: string;
  model_version: string;
  created_at: string;
};

export type Match = {
  id: number;
  external_source: string | null;
  external_id: string | null;
  team_a_id: number;
  team_b_id: number;
  tournament_name: string | null;
  tournament_tier: string | null;
  start_time: string | null;
  format: string | null;
  status: string;
  winner_team_id: number | null;
  is_draw: boolean;
  is_tier1_match: boolean;
  excluded_reason: string | null;
  dataset_profile?: string | null;
  competition_tier?: string | null;
  verification_status?: string | null;
  source_confidence?: string | null;
  is_training_eligible?: boolean | null;
  is_prediction_eligible?: boolean | null;
  prediction_block_reason?: string | null;
  prediction_guard_level?: string | null;
  created_at: string;
  updated_at: string;
  team_a: Team;
  team_b: Team;
};

export type MatchDetail = Match & {
  stats: TeamMatchStats[];
  predictions: Prediction[];
};

export type MatchForecastSnapshot = {
  id: number;
  horizon_bucket: "early" | "day_before" | "final" | string;
  is_primary: boolean;
  generated_at: string;
  scheduled_start: string;
  lead_time_hours: number;
  prediction_type: string;
  model_version: string;
  team_a_probability: number;
  team_b_probability: number;
  confidence: string;
  confidence_score: number;
  status: string;
  actual_outcome: "team_a" | "team_b" | "draw" | null;
  correct: boolean | null;
  log_loss: number | null;
  brier_score: number | null;
};

export type MatchForecastHistory = {
  match_id: number;
  match_status: string;
  actual_outcome: "team_a" | "team_b" | "draw" | null;
  winner_team_id: number | null;
  winner_team_name: string | null;
  prospective_snapshot_available: boolean;
  preferred_snapshot: MatchForecastSnapshot | null;
  forecasts: MatchForecastSnapshot[];
};

export type MatchTeamContext = {
  team_id: number;
  roster_count: number;
  roster_known: boolean;
  roster_ambiguous: boolean;
  roster_stability_known: boolean;
  roster_stability_days: number | null;
  same_roster_matches_count: number;
  has_recent_roster_change: boolean;
  recent_standins_count: number;
};

export type MatchContext = {
  patch: {
    id: number;
    patch_name: string;
    patch_version: string;
    release_date: string;
    is_current: boolean;
  } | null;
  days_since_patch: number | null;
  is_current_patch: boolean;
  teams: {
    team_a: MatchTeamContext;
    team_b: MatchTeamContext;
  };
};

export type MatchDraft = {
  match_id: number;
  draft_available: boolean;
  draft_complete: boolean;
  team_a_picks_count: number;
  team_b_picks_count: number;
  team_a_bans_count: number;
  team_b_bans_count: number;
  entries: Array<{
    id: number;
    team_id: number;
    hero_id: number;
    action_type: "pick" | "ban" | string;
    pick_order: number | null;
    ban_order: number | null;
    draft_order: number;
    side: string;
    hero: {
      id: number;
      hero_id: number;
      localized_name: string;
      name: string;
    } | null;
  }>;
};

export type DraftFeaturesResponse = {
  experimental: boolean;
  features: Record<string, number | string | boolean | null>;
};

export type ExplanationFactor = {
  factor: string;
  impact: number;
  text: string;
};

export type PredictionExplanation = {
  summary?: string;
  positive_factors?: ExplanationFactor[];
  negative_factors?: ExplanationFactor[];
  component_summary?: string[];
  raw_feature_values?: Record<string, unknown>;
};

export type EnsembleComponent = {
  available: boolean;
  team_a_probability?: number | null;
  weight: number;
  model_version?: string | null;
  unavailable_reason?: string | null;
};

export type PredictionTeamAnalytics = {
  elo_rating: number | null;
  glicko_rating: number | null;
  rating_uncertainty: number | null;
  recent_form: number;
  matches_count: number;
  roster_count: number;
  stats_count: number;
};

export type PredictionAnalyticsContext = {
  history_scope: string;
  cutoff: string | null;
  uses_only_past_matches: boolean;
  identity_resolution: string;
  dev_seed_included: boolean;
  head_to_head_matches: number;
  team_a: PredictionTeamAnalytics;
  team_b: PredictionTeamAnalytics;
};

export type FormulaPrediction = {
  match_id: string;
  prediction_type: string;
  model_version: string;
  team_a_probability: number;
  team_b_probability: number;
  confidence: "low" | "medium" | "high";
  confidence_score: number;
  factors: {
    recent_form: number;
    team_rating: number;
    head_to_head: number;
    hero_pool: number;
    roster_stability: number;
  };
  explanation?: string[] | PredictionExplanation;
  warning: string;
  fallback_used: boolean;
  fallback_reason: string | null;
  data_freshness: {
    features_generated_at: string | null;
    model_trained_at: string | null;
    runtime_feature_adapter?: string | null;
  } | null;
  components?: Record<"formula" | "elo" | "ml", EnsembleComponent>;
  weights?: Record<string, number>;
  component_summary?: string[];
  confidence_guard_applied?: boolean;
  confidence_reasons?: string[];
  original_probability_before_guard?: number | null;
  weight_source?: "default" | "backtest" | string | null;
  weight_reason?: string | null;
  backtest_metrics_used?: boolean;
  walk_forward_metrics_used?: boolean;
  probability_unit?: "map_strength" | string;
  series_outcomes?: {
    format: "BO1" | "BO2" | "BO3" | "BO5" | string;
    probability_unit: "series_outcome" | string;
    team_a_win: number;
    draw: number;
    team_b_win: number;
    method: string;
    assumption_warning: string;
  } | null;
  analytics_context?: PredictionAnalyticsContext | null;
};

export type MarketOutcomeValue = {
  outcome: "team_a" | "draw" | "team_b" | string;
  model_probability: number;
  decimal_odds: number;
  implied_probability: number;
  no_vig_probability: number;
  edge: number;
  expected_value: number;
};

export type MarketEvaluationResponse = {
  match_id: number;
  bookmaker: string;
  market_type: string;
  overround: number;
  outcomes: MarketOutcomeValue[];
  best_outcome: string | null;
  paper_test_eligible: boolean;
  recommendation: string;
  guard_reasons: string[];
  paper_bet_id: number | null;
  warning: string;
};

export type PaperBetsSummary = {
  total_bets: number;
  pending_bets: number;
  settled_bets: number;
  won_bets: number;
  lost_bets: number;
  void_bets: number;
  total_profit_units: number;
  total_staked_units: number;
  hit_rate: number | null;
  roi: number | null;
  by_status: Record<string, number>;
  real_bets_placed: boolean;
};

export type TeamRating = {
  team_id: string;
  rating_type: string;
  rating_value: number;
  uncertainty: number;
  matches_count: number;
  calculated_at: string;
};

export type ModelVersion = {
  id: number;
  model_name: string;
  model_type: string;
  version: string;
  trained_at: string;
  metrics_json: Record<string, unknown> | null;
  artifact_path: string;
  is_active: boolean;
  status?: "candidate" | "active" | "archived" | "rejected" | string;
  promoted_at?: string | null;
  rejected_at?: string | null;
  promotion_reason?: string | null;
  artifact_metadata_json?: Record<string, unknown> | null;
  dev_seed_warning?: string | null;
};

export type Backtest = {
  id: number;
  model_version_id: number | null;
  model_version?: string | null;
  model_status?: string | null;
  started_at: string;
  finished_at: string | null;
  date_from: string | null;
  date_to: string | null;
  dataset_type: string;
  matches_count: number;
  metrics_json: Record<string, unknown> | null;
  report_path: string;
  warning: string | null;
};

export type ProspectiveAccuracyReport = {
  status: string;
  generated_at: string;
  total_forecasts: number;
  total_matches?: number;
  pending_forecasts: number;
  settled_forecasts: number;
  primary_horizon?: "final";
  primary_pending_forecasts?: number;
  primary_settled_forecasts?: number;
  metrics: {
    sample_size: number;
    accuracy: number | null;
    log_loss: number | null;
    brier_score: number | null;
  };
  by_confidence: Record<string, {
    sample_size: number;
    accuracy: number | null;
    log_loss: number | null;
    brier_score: number | null;
  }>;
  by_horizon?: Record<"early" | "day_before" | "final", {
    total: number;
    pending: number;
    settled: number;
    metrics: {
      sample_size: number;
      accuracy: number | null;
      log_loss: number | null;
      brier_score: number | null;
    };
    by_format?: Record<string, ProspectiveMetricSummary>;
    component_metrics?: Record<string, ProspectiveMetricSummary>;
  }>;
  by_format?: Record<string, ProspectiveMetricSummary>;
  by_tournament?: Record<string, ProspectiveMetricSummary>;
  by_prediction_type?: Record<string, ProspectiveMetricSummary>;
  component_metrics?: Record<string, ProspectiveMetricSummary>;
  all_horizons_component_metrics?: Record<string, ProspectiveMetricSummary>;
  coverage?: {
    tracked_settled_matches: number;
    final_settled_matches: number;
    final_capture_rate: number | null;
    minimum_final_forecasts: number;
    recommended_final_forecasts: number;
  };
  quality_gates?: {
    final_sample_size: "collecting" | "passed" | string;
    final_capture_rate: "collecting" | "passed" | string;
    betting_claims_allowed: boolean;
  };
  verified_pro_preview?: {
    status: string;
    isolated_from_strict_metrics: boolean;
    used_for_training: boolean;
    used_for_promotion: boolean;
    total_forecasts: number;
    pending_forecasts: number;
    settled_forecasts: number;
    primary_pending_forecasts: number;
    primary_settled_forecasts: number;
    metrics: ProspectiveMetricSummary;
    all_horizons_metrics: ProspectiveMetricSummary;
    component_metrics?: Record<"ensemble" | "formula" | "elo" | "ml", ProspectiveMetricSummary>;
    all_horizons_component_metrics?: Record<"ensemble" | "formula" | "elo" | "ml", ProspectiveMetricSummary>;
    by_horizon: Record<"early" | "day_before" | "final", {
      total: number;
      pending: number;
      settled: number;
      metrics: ProspectiveMetricSummary;
      component_metrics?: Record<"ensemble" | "formula" | "elo" | "ml", ProspectiveMetricSummary>;
    }>;
    coverage: {
      tracked_settled_matches: number;
      final_settled_matches: number;
      final_capture_rate: number | null;
      minimum_final_forecasts: number;
    };
    warning: string | null;
  };
  warning: string | null;
};

export type ProspectiveDecisionReport = {
  status: string;
  decision_status: "collecting" | "review_required" | string;
  generated_at: string;
  strict_final_forecasts: number;
  minimum_final_forecasts: number;
  recommended_final_forecasts: number;
  remaining_to_minimum: number;
  final_capture_rate: number | null;
  minimum_final_capture_rate: number;
  component_samples: Record<"ensemble" | "formula" | "elo" | "ml", number>;
  component_metrics: Record<"ensemble" | "formula" | "elo" | "ml", ProspectiveMetricSummary>;
  best_by_log_loss: string | null;
  best_by_brier_score: string | null;
  recommended_action: string;
  reasons: string[];
  candidate_training_allowed: boolean;
  automatic_training_enabled: boolean;
  promotion_allowed: boolean;
  automatic_promotion_enabled: boolean;
  betting_claims_allowed: boolean;
  verified_pro_preview_used: boolean;
  warning: string | null;
};

export type ProspectiveMetricSummary = {
  sample_size: number;
  accuracy: number | null;
  log_loss: number | null;
  brier_score: number | null;
};

export type ForecastHealthReport = {
  status: string;
  generated_at: string;
  summary: {
    upcoming_prediction_eligible?: number;
    missing_current_horizon_snapshots?: number;
    missing_final_snapshots?: number;
    tracked_finished_matches?: number;
    historical_missing_final_snapshots?: number;
    schedule_drift_forecasts?: number;
    pending_settlement_gaps?: number;
    refresh_status?: string;
    refresh_age_minutes?: number | null;
    refresh_stale?: boolean;
    refresh_stale_after_minutes?: number;
  };
  checks: Record<string, string>;
  warnings: string[];
  errors: string[];
  missing_snapshots: Array<{
    match_id: number;
    external_id?: string | null;
    teams: string;
    tournament?: string | null;
    start_time?: string | null;
    lead_time_hours: number;
    missing_horizon: string;
    existing_horizons: string[];
    severity: string;
    command_hint?: string;
  }>;
  historical_final_gaps?: Array<{
    match_id: number;
    external_id?: string | null;
    teams: string;
    tournament?: string | null;
    start_time?: string | null;
    reason: string;
  }>;
  schedule_drift_gaps?: Array<{
    match_id: number;
    horizon_bucket: string;
    teams: string;
    forecast_scheduled_start?: string | null;
    current_scheduled_start?: string | null;
    drift_minutes: number;
    reason: string;
  }>;
  settlement_gaps: Array<{
    forecast_id: number;
    match_id: number;
    horizon_bucket: string;
    teams: string;
    scheduled_start?: string | null;
    status: string;
    command_hint?: string;
  }>;
  latest_refresh?: {
    status?: string;
    cycle_status?: string;
    generated_at?: string;
    errors?: string[];
    steps?: Record<string, Record<string, unknown>>;
    forecast_health?: Record<string, unknown>;
    message?: string;
  };
  command_hints?: string[];
};

export type DraftExperimentReport = {
  status: string;
  draft_candidates: ModelVersion[];
  latest_draft_backtest: {
    status?: string;
    dataset_type?: string;
    sample_size?: number;
    candidate_version?: string | null;
    compared_models?: string[];
    metrics?: Record<string, Record<string, number | null>>;
    best_by_log_loss?: string | null;
    best_by_brier_score?: string | null;
    warnings?: string[];
    draft_model_used?: boolean;
    not_used_in_main_prediction?: boolean;
  } | null;
  sample_size: number | null;
  warnings: string[];
  promotion_enabled: boolean;
  not_used_in_main_prediction: boolean;
};

export type DataSourceStatus = {
  enabled: boolean;
  has_api_key: boolean;
  last_sync_status: "ok" | "failed" | "never" | string;
  last_error: string | null;
  setup_hint?: string | null;
  missing_key_reason?: string | null;
  safe_to_sync?: boolean;
  capabilities?: Record<string, boolean | string>;
};

export type DataSourcesStatusResponse = {
  sources: Record<string, DataSourceStatus>;
  capabilities?: Record<string, Record<string, boolean | string>>;
};

export type RealIngestionPlan = {
  status: string;
  generated_at?: string;
  message?: string;
  available_sources?: string[];
  missing_keys?: string[];
  coverage?: {
    tier1_historical_matches_count?: number;
    real_tier1_historical_matches_count?: number;
    dev_seed_only?: boolean;
    training_readiness?: string;
    usable_threshold_remaining?: number;
    good_threshold_remaining?: number;
  };
  recommended_commands?: string[];
  blockers?: string[];
};

export type ImportQualityReport = {
  status: string;
  generated_at?: string;
  message?: string;
  file?: string;
  rows_seen?: number;
  estimated_valid_rows?: number;
  estimated_excluded_rows?: number;
  reason_counts?: Record<string, number>;
  warnings?: string[];
  errors?: string[];
};

export type RealBatchReport = {
  status: string;
  generated_at?: string;
  message?: string;
  real_matches_before?: number | null;
  real_matches_after?: number | null;
  imported_rows?: number;
  would_import_rows?: number;
  excluded_rows?: number;
  coverage_readiness?: string;
  dev_seed_only?: boolean;
  dataset_type?: string;
  candidate_created?: boolean;
  candidate_version?: string | null;
  backtest_status?: string;
  best_by_log_loss?: string | null;
  best_by_brier_score?: string | null;
  warnings?: string[];
  errors?: string[];
  recommended_next_step?: string;
};

export type StratzMatchIdValidationReport = {
  status: string;
  generated_at?: string;
  file?: string;
  message?: string;
  rows_seen?: number;
  valid_match_ids?: string[];
  invalid_match_ids?: string[];
  mismatched_expected_fields?: Array<Record<string, string>>;
  tier1_valid_count?: number;
  safe_to_apply?: boolean;
  errors?: string[];
  warnings?: string[];
};

export type StratzMatchIdImportReport = {
  status: string;
  generated_at?: string;
  file?: string;
  message?: string;
  mode?: string;
  records_seen?: number;
  would_create?: number;
  would_update?: number;
  would_exclude?: number;
  records_created?: number;
  records_updated?: number;
  records_excluded?: number;
  validation_status?: string;
  safe_to_apply?: boolean;
  apply_allowed?: boolean;
  apply_block_reason?: string | null;
  draft_imported_count?: number;
  source_errors?: string[];
  warnings?: string[];
};

export type UpcomingSyncReport = {
  status: string;
  message?: string;
  source?: string;
  records_seen?: number;
  would_create?: number;
  would_update?: number;
  would_exclude?: number;
  upcoming_count?: number;
  saved_upcoming_candidates?: number;
  truly_invalid_count?: number;
  tier1_upcoming_count?: number;
  pro_upcoming_count?: number;
  qualifier_upcoming_count?: number;
  academy_upcoming_count?: number;
  unknown_upcoming_count?: number;
  prediction_eligible_count?: number;
  prediction_blocked_count?: number;
  missing_team_count?: number;
  missing_tournament_count?: number;
  source_errors?: string[];
  warnings?: string[];
  hard_exclusion_reasons?: Record<string, number>;
  classification_reasons?: Record<string, number>;
  top_prediction_block_reasons?: Record<string, number>;
  apply_allowed?: boolean;
  recommendation?: string;
};

export type MatchDetailEnrichmentReport = {
  status: string;
  message?: string;
  generated_at?: string;
  mode?: "dry_run" | "apply" | string;
  source?: string;
  records_seen?: number;
  details_fetched?: number;
  would_enrich?: number;
  matches_enriched?: number;
  records_excluded?: number;
  skipped_existing?: number;
  rate_limit_retries_used?: number;
  stats_rows_created?: number;
  stats_rows_updated?: number;
  draft_entries_created?: number;
  draft_entries_updated?: number;
  draft_snapshots_created?: number;
  draft_snapshots_updated?: number;
  total_enriched_matches?: number;
  total_stats_rows?: number;
  total_draft_entries?: number;
  total_draft_snapshots?: number;
  source_errors?: string[];
  warnings?: string[];
  training_changed?: boolean;
  promotion_changed?: boolean;
  recommendation?: string;
};

export type UpcomingMatchSearchResponse = {
  items: UpcomingMatchItem[];
  total: number;
  limit: number;
  offset: number;
  tournament_options?: Array<{
    name: string;
    match_count: number;
    live_count: number;
    upcoming_count: number;
  }>;
  scope_summary?: {
    strict_prediction_count: number;
    verified_pro_preview_count: number;
    blocked_count: number;
    training_eligible_count: number;
  };
};

export type UpcomingMatchItem = {
  id: number;
  external_id: string | null;
  source: string | null;
  team_a: { id: number; name: string } | null;
  team_b: { id: number; name: string } | null;
  tournament: string | null;
  start_time: string | null;
  status: string;
  format: string | null;
  dataset_profile?: string | null;
  competition_tier?: string | null;
  verification_status: string;
  source_confidence: string;
  source_prediction_eligible?: boolean;
  prediction_eligible: boolean;
  preview_eligible?: boolean;
  analysis_mode?: "strict_prediction" | "verified_pro_preview" | "blocked" | string;
  prediction_block_reason: string | null;
  prediction_guard_level?: string | null;
  is_training_eligible: boolean;
  decision_status?: "needs_odds" | "watch" | "skip" | "preview" | "blocked" | string;
  decision_reason?: string | null;
  decision_reasons?: string[];
  prediction_summary?: {
    prediction_type: string;
    team_a_probability: number;
    team_b_probability: number;
    probability_unit?: "map_strength" | string | null;
    confidence: string;
    confidence_score: number;
    confidence_guard_applied?: boolean;
    best_side: "team_a" | "team_b" | string;
    weight_source?: string | null;
    series_outcomes?: FormulaPrediction["series_outcomes"];
  } | null;
};

export type SourceHealthReport = {
  status: string;
  message?: string;
  sources?: Record<string, { enabled?: boolean; has_api_key?: boolean; can_connect?: boolean | null; last_error?: string | null; rate_limit_warning?: string | null }>;
  warnings?: string[];
};

export type HistoricalFetchPlan = {
  status: string;
  message?: string;
  available_sources?: string[];
  recommended_windows?: Array<{ label: string; start_date: string; end_date: string }>;
  blockers?: string[];
  command_hints?: string[];
};

export type SyncReviewReport = {
  status: string;
  message?: string;
  source?: string | null;
  records_seen?: number;
  would_create?: number;
  would_update?: number;
  would_exclude?: number;
  valid_rows?: number;
  top_exclusion_reasons?: Record<string, number>;
  unknown_teams?: string[];
  unknown_tournaments?: string[];
  alias_suggestions?: {
    teams?: Record<string, AliasSuggestion[]>;
    tournaments?: Record<string, AliasSuggestion[]>;
  };
  blocked_alias_suggestions?: AliasSuggestion[];
  risky_alias_suggestions?: AliasSuggestion[];
  safe_alias_suggestions?: AliasSuggestion[];
  source_trust_level?: string;
  apply_allowed?: boolean;
  apply_block_reason?: string | null;
  recommended_action?: string;
  recommendation_detail?: string;
};

export type AliasSuggestion = {
  kind?: string;
  raw_name: string;
  suggested_canonical: string;
  risk: "blocked" | "risky" | "safe" | string;
  reason: string;
};

export type SourceMappingStatus = {
  status: string;
  message?: string;
  mapping_path?: string;
  mapped_teams_count?: number;
  mapped_tournaments_count?: number;
  invalid_mappings_count?: number;
  invalid_mappings?: Array<{
    source: string;
    kind: string;
    key: string;
    canonical_name: string;
  }>;
};

export type SyncLog = {
  id: number;
  source: string;
  sync_type: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  records_seen: number;
  records_created: number;
  records_updated: number;
  records_excluded: number;
  error_message: string | null;
  metadata_json: Record<string, unknown> | null;
};

export type DataCoverageReport = {
  generated_at: string | null;
  tier1_teams_count: number;
  tier1_historical_matches_count: number;
  real_tier1_historical_matches_count?: number;
  verified_pro_historical_matches_count?: number;
  real_training_eligible_matches_count?: number;
  dev_seed_historical_matches_count?: number;
  matches_with_winner_count: number;
  matches_with_patch_context_count: number;
  matches_with_roster_context_count: number;
  patch_coverage_ratio: number;
  roster_coverage_ratio: number;
  matches_by_tournament: Record<string, number>;
  matches_by_patch: Record<string, number>;
  matches_by_source: Record<string, number>;
  date_range: {
    from: string | null;
    to: string | null;
  };
  training_readiness: "insufficient" | "usable" | "good" | string;
  enough_for_training: boolean;
  dev_seed_only: boolean;
  warning: string | null;
};

export type ProjectAuditReport = {
  status: "ok" | "warning" | "failed" | "missing" | string;
  message?: string;
  summary?: {
    generated_at?: string;
    total_teams?: number;
    active_tier1_teams?: number;
    total_matches?: number;
    tier1_matches?: number;
    finished_tier1_matches?: number;
    upcoming_tier1_matches?: number;
    excluded_matches?: number;
    dev_seed_matches?: number;
    real_source_matches?: number;
    coverage?: {
      training_readiness?: string;
      tier1_historical_matches_count?: number;
      patch_coverage_ratio?: number;
      roster_coverage_ratio?: number;
      dev_seed_only?: boolean;
    };
    model?: {
      active_model_id?: number | null;
      active_model_status?: string | null;
      artifacts_exist?: boolean;
      artifacts_readable?: boolean;
      latest_backtest_dataset_type?: string | null;
    };
  };
  warnings?: string[];
  errors?: string[];
  checks?: Record<string, string>;
};

export type SystemReadiness = {
  status: "ok" | "warning" | string;
  ready: boolean;
  generated_at: string;
  service: string;
  active_model_version: string | null;
  scheduler_age_minutes: number | null;
  real_tier1_matches: number | null;
  verified_pro_matches: number | null;
  warnings: string[];
  checks: Record<string, {
    status: string;
    message?: string | null;
    [key: string]: unknown;
  }>;
};

export type MatchValidationReport = {
  status: "ok" | "warning" | "failed" | "missing" | string;
  message?: string;
  generated_at?: string;
  summary?: {
    total_matches?: number;
    tier1_matches?: number;
    excluded_matches?: number;
    suspect_matches_count?: number;
    external_source_distribution?: Record<string, number>;
  };
  source_summary?: Record<
    string,
    {
      total_matches: number;
      valid_matches: number;
      invalid_matches: number;
      excluded_matches: number;
      duplicate_warnings: number;
      missing_winner_count: number;
      missing_tournament_count: number;
      unknown_team_count: number;
    }
  >;
  warnings?: string[];
  errors?: string[];
  suspect_matches?: Array<{
    match_id: number;
    external_source: string | null;
    external_id: string | null;
    teams: Array<string | null>;
    tournament: string | null;
    start_time: string | null;
    reason: string;
  }>;
};

export const apiBaseUrl =
  typeof window === "undefined"
    ? process.env.BACKEND_INTERNAL_URL ?? process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000"
    : process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

export class BackendApiError extends Error {
  constructor(public readonly status: number) {
    super(`Backend request failed with status ${status}`);
    this.name = "BackendApiError";
  }
}

export async function fetchFromBackend<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, init);

  if (!response.ok) {
    throw new BackendApiError(response.status);
  }

  return response.json() as Promise<T>;
}

export function formatMatchDate(value: string | null): string {
  if (!value) {
    return "Time TBD";
  }

  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(new Date(value));
}

export function formatMatchFormat(value: string | null): string {
  if (!value) {
    return "Format TBD";
  }

  return value.toUpperCase();
}

export function getTeamInitials(name: string): string {
  return name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}
