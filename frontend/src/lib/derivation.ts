import type { Derivation, DerivationState, DerivationSummary } from "./api-types";

const UNKNOWN: Derivation = { state: "unknown", reason: "unbound", changed_count: 0, missing_count: 0 };
const STATES: DerivationState[] = ["active", "stale", "missing", "unknown"];
const count = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;

/** Old saved responses lack the DTO. They cannot establish matching inputs. */
export function normalizeDerivation(value: Derivation | null | undefined): Derivation {
  return value && STATES.includes(value.state) && typeof value.reason === "string"
    && count(value.changed_count) && count(value.missing_count) ? value : UNKNOWN;
}

export function pendingRelations(summary: DerivationSummary | undefined): number | null {
  if (!summary || ![summary.clusters, summary.conflicts].every((group) =>
    group && STATES.every((state) => count(group[state])))) return null;
  return [summary.clusters, summary.conflicts].reduce((total, group) => total + group.stale + group.missing + group.unknown, 0);
}
