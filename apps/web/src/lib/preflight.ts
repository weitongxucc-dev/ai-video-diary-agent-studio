import type {
  SetupPreflightCheckStatus,
  SetupPreflightOverall,
  SetupPreflightResponse,
} from "./types";

/**
 * Normalized state vocabulary consumed by the future onboarding UI. It is
 * intentionally decoupled from the raw wire statuses so the UI only has to
 * reason about four outcomes:
 *   - ready          the check passed
 *   - attention      the check degraded but is not blocking
 *   - blocked        the check is missing or invalid and blocks onboarding
 *   - not_applicable the check is optional and skipped
 */
export type SetupPreflightState =
  | "ready"
  | "attention"
  | "blocked"
  | "not_applicable";

export type NormalizedSetupPreflightCheck = {
  id: string;
  label: string;
  status: SetupPreflightCheckStatus;
  state: SetupPreflightState;
  summary: string;
  impact: string;
  repairAction: string;
  settingsTarget: string | null;
};

export type SetupPreflightCounts = {
  total: number;
  ready: number;
  attention: number;
  blocked: number;
  notApplicable: number;
};

export type NormalizedSetupPreflight = {
  overall: SetupPreflightOverall;
  ready: boolean;
  generatedAt: string | null;
  generatedAtMs: number | null;
  counts: SetupPreflightCounts;
  checks: NormalizedSetupPreflightCheck[];
};

const VALID_CHECK_STATUSES: ReadonlySet<string> = new Set([
  "ready",
  "missing",
  "invalid",
  "degraded",
  "not_applicable",
]);

const STATE_BY_STATUS: Record<SetupPreflightCheckStatus, SetupPreflightState> = {
  ready: "ready",
  degraded: "attention",
  invalid: "blocked",
  missing: "blocked",
  not_applicable: "not_applicable",
};

function coerceString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function coerceCheckStatus(value: unknown): SetupPreflightCheckStatus {
  if (typeof value === "string" && VALID_CHECK_STATUSES.has(value)) {
    return value as SetupPreflightCheckStatus;
  }
  // Unknown/malformed statuses are treated as invalid so the UI surfaces
  // them as blocking rather than silently dropping the check.
  return "invalid";
}

function coerceSettingsTarget(value: unknown): string | null {
  if (value === null || value === undefined) {
    return null;
  }
  const text = coerceString(value);
  return text.length > 0 ? text : null;
}

function parseGeneratedAt(value: unknown): {
  iso: string | null;
  ms: number | null;
} {
  if (typeof value !== "string" || value.length === 0) {
    return { iso: null, ms: null };
  }
  const ms = Date.parse(value);
  if (!Number.isFinite(ms)) {
    return { iso: null, ms: null };
  }
  return { iso: value, ms };
}

function normalizeCheck(raw: unknown): NormalizedSetupPreflightCheck {
  const item = (raw ?? {}) as Record<string, unknown>;
  const status = coerceCheckStatus(item.status);
  return {
    id: coerceString(item.id),
    label: coerceString(item.label),
    status,
    state: STATE_BY_STATUS[status],
    summary: coerceString(item.summary),
    impact: coerceString(item.impact),
    repairAction: coerceString(item.repair_action),
    settingsTarget: coerceSettingsTarget(item.settings_target),
  };
}

/**
 * Convert the raw GET /api/setup/preflight payload into a UI-consumable state.
 *
 * This is a pure function: given the same input it returns the same output
 * and performs no I/O. It is defensive against a null/malformed payload so
 * callers can always render a complete (possibly empty) snapshot.
 */
export function normalizeSetupPreflight(
  raw: SetupPreflightResponse | null | undefined,
): NormalizedSetupPreflight {
  const rawChecks = Array.isArray(raw?.checks) ? raw.checks : [];
  const checks = rawChecks.map(normalizeCheck);

  const counts = checks.reduce<SetupPreflightCounts>(
    (acc, check) => {
      acc.total += 1;
      if (check.state === "ready") acc.ready += 1;
      else if (check.state === "attention") acc.attention += 1;
      else if (check.state === "blocked") acc.blocked += 1;
      else acc.notApplicable += 1;
      return acc;
    },
    { total: 0, ready: 0, attention: 0, blocked: 0, notApplicable: 0 },
  );

  const overall: SetupPreflightOverall =
    raw?.overall === "ready" ? "ready" : "blocked";
  const { iso, ms } = parseGeneratedAt(raw?.generated_at);

  return {
    overall,
    ready: overall === "ready",
    generatedAt: iso,
    generatedAtMs: ms,
    counts,
    checks,
  };
}
