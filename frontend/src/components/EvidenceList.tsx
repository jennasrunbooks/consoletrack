import type { Evidence } from "../api";
import { callSignature, readableResult } from "../lib/format";
import type { Resource } from "../lib/useResource";

/**
 * The agent's tool calls, oldest first, each collapsed to its signature. Results are redacted
 * but untrusted (pod logs, CloudTrail text), so they are rendered as text in a <pre>, never HTML.
 */
export function EvidenceList({ evidence }: { evidence: Resource<{ items: Evidence[] }> }) {
  const { data, error, reload } = evidence;
  if (error && !data) {
    return (
      <div className="card-state">
        <p role="alert">Couldn't load the evidence: {error.message}</p>
        <button type="button" className="button button-secondary" onClick={reload}>
          Retry
        </button>
      </div>
    );
  }
  if (!data) return <p className="muted" role="status">Loading evidence…</p>;
  if (data.items.length === 0) return <p className="muted">No tool calls recorded yet.</p>;

  const runs = [...new Set(data.items.map((e) => e.agent_run_id))];
  return (
    <ol className="evidence">
      {data.items.map((e, index) => {
        const newRun = runs.length > 1 && (index === 0 || data.items[index - 1]!.agent_run_id !== e.agent_run_id);
        return (
          <li key={e.id}>
            {newRun && <p className="evidence-run">Run {runs.indexOf(e.agent_run_id) + 1} of {runs.length}</p>}
            <details className="evidence-item">
              <summary>
                <span className="evidence-seq" aria-hidden="true">
                  {e.seq}
                </span>
                <code className="evidence-call">{callSignature(e)}</code>
              </summary>
              <pre className="evidence-result" aria-label={`Result of ${e.tool_name}`}>
                {e.result_excerpt ? readableResult(e.result_excerpt) : "(empty result)"}
              </pre>
            </details>
          </li>
        );
      })}
    </ol>
  );
}
