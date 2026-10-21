export interface ValueChange {
  path: string;
  before: string | null;
  after: string | null;
}

function flatten(value: unknown, prefix: string, out: Map<string, string>): void {
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    for (const [key, child] of Object.entries(value)) flatten(child, prefix ? `${prefix}.${key}` : key, out);
  } else if (prefix) {
    out.set(prefix, typeof value === "string" ? value : JSON.stringify(value));
  }
}

/**
 * A remediation's `before` and `after` as one line per value the patch sets, e.g.
 * `limits.memory 128Mi → 256Mi`. Values only in `before` aren't changed by the patch.
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
  return [...a].map(([path, value]) => ({ path, before: b.get(path) ?? null, after: value }));
}
