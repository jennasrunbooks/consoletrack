import type { ReactNode } from "react";
import { ApiError } from "../api";

/** A message after an action; `unavailable` is a 501, i.e. the backend can't do it yet. */
export type Outcome = { kind: "error" | "unavailable" | "done"; text: ReactNode } | null;

export function outcomeFor(err: unknown): Outcome {
  if (err instanceof ApiError && err.status === 501) {
    return { kind: "unavailable", text: `Not available yet: ${err.message}. Nothing was changed.` };
  }
  return { kind: "error", text: err instanceof Error ? err.message : String(err) };
}
