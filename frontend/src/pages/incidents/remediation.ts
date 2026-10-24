import type { Remediation } from "../../api";

export interface ValueChange {
  path: string;
  label: string;
  before: string | null;
  after: string | null;
}

/** Plain names for the values a patch sets; any other path is shown as is. */
const VALUE_LABELS: Record<string, string> = {
  "limits.memory": "memory limit",
  "requests.memory": "memory request",
};

function flatten(value: unknown, prefix: string, out: Map<string, string>): void {
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    for (const [key, child] of Object.entries(value)) flatten(child, prefix ? `${prefix}.${key}` : key, out);
  } else if (prefix) {
    out.set(prefix, typeof value === "string" ? value : JSON.stringify(value));
  }
}

/**
 * A remediation's `before` and `after` as one line per value the patch sets, e.g.
 * `memory limit 128Mi → 256Mi`. Values only in `before` aren't changed by the patch.
 */
export function valueChanges(
  before: Record<string, unknown> | null | undefined,
  after: Record<string, unknown> | null | undefined,
): ValueChange[] {
  if (!after) return [];
  const b = new Map<string, string>();
  const a = new Map<string, string>();
  flatten(before ?? {}, "", b);
  flatten(after, "", a);
  return [...a].map(([path, value]) => ({
    path,
    label: VALUE_LABELS[path] ?? path,
    before: b.get(path) ?? null,
    after: value,
  }));
}

export const changeText = (c: ValueChange): string => `${c.label} ${c.before ?? "unset"} → ${c.after ?? "unset"}`;

export interface RemediationResult {
  kind: "fixed" | "rolled_back" | "rollback_failed";
  text: string;
}

/**
 * The outcome as one line for the top of the fix, e.g. "Fixed automatically: memory limit
 * 128Mi → 256Mi", or the rollback when verify failed (openapi.yaml → Remediation.steps).
 */
export function remediationResult(
  r: Pick<Remediation, "status" | "mode" | "before" | "after" | "steps">,
): RemediationResult | null {
  const rollback = r.steps.findLast((s) => s.step === "rollback");
  if (rollback) {
    return rollback.ok
      ? { kind: "rolled_back", text: `Rolled back: ${rollback.detail || "the patch was undone"}` }
      : { kind: "rollback_failed", text: `Rollback failed: ${rollback.detail || "a human must fix it"}` };
  }
  if (r.status !== "succeeded") return null;
  const changed = valueChanges(r.before, r.after).map(changeText).join(", ");
  const fixed = r.mode === "auto" ? "Fixed automatically" : "Fixed";
  return { kind: "fixed", text: changed ? `${fixed}: ${changed}` : fixed };
}
