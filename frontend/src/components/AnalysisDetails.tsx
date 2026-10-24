import type { Analysis } from "../api";
import { duration, FLAG_LABELS, RISK_LABELS, WARNING_FLAGS } from "../lib/format";

function runSeconds(analysis: Analysis): number | null {
  const { started_at, finished_at } = analysis.agent_run;
  if (!finished_at) return null;
  return Math.max(0, Math.round((Date.parse(finished_at) - Date.parse(started_at)) / 1000));
}

/**
 * The agent's structured result: flags, summary, root cause, risk and its reason, and the run
 * ("Confidence: high · 4 tool calls · model · 11 s"). Shared by the change and incident pages;
 * every field is the agent's text, so it is rendered as text.
 */
export function AnalysisDetails({ analysis: a }: { analysis: Analysis }) {
  const seconds = runSeconds(a);
  const run = a.agent_run;
  return (
    <>
      {a.flags.length > 0 && (
        <ul className="flags" aria-label="Flags">
          {a.flags.map((flag) => (
            <li key={flag} className={`pill ${WARNING_FLAGS.has(flag) ? "flag-warning" : "flag-info"}`}>
              {FLAG_LABELS[flag] ?? flag}
            </li>
          ))}
        </ul>
      )}
      <p>{a.summary}</p>
      {a.root_cause && (
        <p>
          <strong>Root cause.</strong> {a.root_cause}
        </p>
      )}
      {(a.risk || a.risk_reason) && (
        <p>
          <strong>Risk{a.risk ? `: ${RISK_LABELS[a.risk].toLowerCase()}` : ""}.</strong> {a.risk_reason}
        </p>
      )}
      <p className="analysis-meta">
        Confidence: {a.confidence} · {run.tool_calls} tool {run.tool_calls === 1 ? "call" : "calls"} ·{" "}
        <span className="mono">{run.model}</span>
        {seconds !== null && ` · ${duration(seconds)}`}
        {run.prompt_version && ` · prompt ${run.prompt_version}`}
        {run.escalated && " · escalated"}
        {run.outcome !== "succeeded" && run.outcome !== "running" && ` · run ${run.outcome.replace("_", " ")}`}
      </p>
    </>
  );
}
