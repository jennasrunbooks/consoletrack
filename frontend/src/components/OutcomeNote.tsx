import type { Outcome } from "../lib/outcome";

export function OutcomeNote({ outcome }: { outcome: Outcome }) {
  if (!outcome) return null;
  const className =
    outcome.kind === "error" ? "form-error" : outcome.kind === "unavailable" ? "notice notice-warning" : "notice";
  return (
    <p className={className} role={outcome.kind === "error" ? "alert" : "status"}>
      {outcome.text}
    </p>
  );
}
