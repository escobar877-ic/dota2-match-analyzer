type GuardContext = {
  teamAName?: string | null;
  teamBName?: string | null;
  tournamentName?: string | null;
  status?: string | null;
};

const PLACEHOLDER_TEAMS = new Set(["", "tbd", "to be decided", "to be determined", "unknown"]);

export function formatPredictionGuardReasons(
  value: string | string[] | null | undefined,
  context: GuardContext = {},
): string[] {
  const codes = (Array.isArray(value) ? value : [value])
    .flatMap((item) => String(item ?? "").split(","))
    .map((item) => item.trim())
    .filter(Boolean);
  if (!codes.length) {
    return ["Prediction is not available yet."];
  }

  const teamAUnknown = isPlaceholderTeam(context.teamAName);
  const teamBUnknown = isPlaceholderTeam(context.teamBName);
  const hasTeamGuard = codes.some((code) => code.startsWith("team_a_") || code.startsWith("team_b_") || code === "missing_teams");
  if (teamAUnknown && teamBUnknown && hasTeamGuard) {
    return ["Teams are not announced yet."];
  }

  const reasons: string[] = [];
  let teamAReasonAdded = false;
  let teamBReasonAdded = false;
  for (const code of codes) {
    if (code === "missing_teams") {
      addUnique(reasons, "One or both teams are not announced yet.");
    } else if (code.startsWith("team_a_")) {
      if (!teamAReasonAdded) {
        addUnique(reasons, teamGuardReason(context.teamAName, "Team A"));
        teamAReasonAdded = true;
      }
    } else if (code.startsWith("team_b_")) {
      if (!teamBReasonAdded) {
        addUnique(reasons, teamGuardReason(context.teamBName, "Team B"));
        teamBReasonAdded = true;
      }
    } else if (code === "missing_tournament") {
      addUnique(reasons, "Tournament information is not available yet.");
    } else if (code.includes("tournament_not_tier1") || code === "lower_tier_tournament") {
      addUnique(
        reasons,
        context.tournamentName
          ? `${context.tournamentName} is outside the strict Tier 1 tournament scope.`
          : "Tournament is outside the strict Tier 1 scope.",
      );
    } else if (code === "not_upcoming" || code === "match_already_finished" || code === "historical_match") {
      addUnique(reasons, context.status === "live" ? "Match has already started." : "Match is no longer upcoming.");
    } else if (code === "verified_pro_training_only") {
      addUnique(reasons, "This verified professional match is not eligible for strict Tier 1 prediction.");
    } else if (code === "not_tier1_match") {
      addUnique(reasons, "Match is outside the strict Tier 1 scope.");
    } else if (code === "not_prediction_eligible") {
      addUnique(reasons, "Match does not currently pass prediction eligibility checks.");
    } else if (looksLikeInternalCode(code)) {
      addUnique(reasons, `${humanizeCode(code)}.`);
    } else {
      addUnique(reasons, code);
    }
  }
  return reasons.length ? reasons : ["Prediction is not available yet."];
}

function teamGuardReason(name: string | null | undefined, fallback: string): string {
  if (isPlaceholderTeam(name)) {
    return `${fallback} is not announced yet.`;
  }
  return `${name} is outside the current strict Tier 1 team scope.`;
}

function isPlaceholderTeam(value: string | null | undefined): boolean {
  return PLACEHOLDER_TEAMS.has(String(value ?? "").trim().toLowerCase());
}

function looksLikeInternalCode(value: string): boolean {
  return /^[a-z0-9]+(?:_[a-z0-9]+)+$/.test(value);
}

function humanizeCode(value: string): string {
  const text = value.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function addUnique(values: string[], value: string): void {
  if (!values.includes(value)) {
    values.push(value);
  }
}
