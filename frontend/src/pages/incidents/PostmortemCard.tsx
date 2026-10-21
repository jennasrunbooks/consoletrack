import { useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { savePostmortem, type Incident } from "../../api";
import { useUser } from "../../auth/AuthProvider";
import { AdminButton } from "../../components/AdminButton";
import { Markdown } from "../../components/Markdown";
import { OutcomeNote } from "../../components/OutcomeNote";
import { outcomeFor, type Outcome } from "../../lib/outcome";

/** openapi.yaml → PostmortemRequest.postmortem_md. */
export const POSTMORTEM_MAX = 100_000;
type Tab = "write" | "preview";

/**
 * The postmortem on the incident's latest analysis: the agent's Markdown draft, which an admin
 * edits and saves (PUT /incidents/{id}/postmortem). The preview goes through <Markdown>, which
 * sanitizes it; the source is only ever a textarea's value.
 */
export function PostmortemCard({ incident, onSaved }: { incident: Incident; onSaved: () => void }) {
  const saved = incident.analysis?.postmortem_md ?? "";
  const [draft, setDraft] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const editing = draft !== null;

  return (
    <section className="card" aria-labelledby="postmortem-heading">
      <div className="card-head">
        <h2 id="postmortem-heading">Postmortem</h2>
        {incident.analysis && !editing && (
          <AdminButton
            className="button button-secondary"
            onClick={() => {
              setOutcome(null);
              setDraft(saved);
            }}
          >
            {saved ? "Edit postmortem" : "Write postmortem"}
          </AdminButton>
        )}
      </div>
      {!incident.analysis ? (
        <p className="muted">The agent drafts a postmortem with its analysis. It appears here when triage finishes.</p>
      ) : editing ? (
        <PostmortemEditor
          incidentId={incident.id}
          draft={draft}
          onChange={setDraft}
          onCancel={() => setDraft(null)}
          onSaved={() => {
            setDraft(null);
            setOutcome({ kind: "done", text: "Postmortem saved." });
            onSaved();
          }}
        />
      ) : saved.trim() ? (
        <Markdown source={saved} label="Postmortem" />
      ) : (
        <p className="muted">No postmortem yet. An admin can write one from the agent's analysis and evidence.</p>
      )}
      {!editing && <OutcomeNote outcome={outcome} />}
    </section>
  );
}

function PostmortemEditor({
  incidentId,
  draft,
  onChange,
  onCancel,
  onSaved,
}: {
  incidentId: number;
  draft: string;
  onChange: (value: string) => void;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const { role } = useUser();
  const id = useId();
  const hintId = useId();
  const [tab, setTab] = useState<Tab>("write");
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const tabRefs = { write: useRef<HTMLButtonElement>(null), preview: useRef<HTMLButtonElement>(null) };
  const tooLong = draft.length > POSTMORTEM_MAX;

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (tooLong || role !== "admin") return;
    setBusy(true);
    setOutcome(null);
    try {
      await savePostmortem(incidentId, draft);
      onSaved();
    } catch (err) {
      setOutcome(outcomeFor(err));
    } finally {
      setBusy(false);
    }
  };

  // The WAI-ARIA tabs pattern: arrow keys move between the two tabs and select them.
  const onTabKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight" && e.key !== "Home" && e.key !== "End") return;
    e.preventDefault();
    const next: Tab = e.key === "Home" ? "write" : e.key === "End" ? "preview" : tab === "write" ? "preview" : "write";
    setTab(next);
    tabRefs[next].current?.focus();
  };

  const tabButton = (name: Tab, label: string) => (
    <button
      ref={tabRefs[name]}
      type="button"
      role="tab"
      id={`${id}-${name}-tab`}
      aria-selected={tab === name}
      aria-controls={`${id}-${name}`}
      tabIndex={tab === name ? 0 : -1}
      className="tab"
      onClick={() => setTab(name)}
      onKeyDown={onTabKey}
    >
      {label}
    </button>
  );

  return (
    <form className="postmortem-form" onSubmit={(e) => void onSubmit(e)}>
      <div className="tabs" role="tablist" aria-label="Postmortem editor">
        {tabButton("write", "Write")}
        {tabButton("preview", "Preview")}
      </div>
      <div role="tabpanel" id={`${id}-write`} aria-labelledby={`${id}-write-tab`} hidden={tab !== "write"}>
        <label htmlFor={`${id}-source`} className="visually-hidden">
          Postmortem (Markdown)
        </label>
        <textarea
          id={`${id}-source`}
          className="mono postmortem-source"
          rows={18}
          value={draft}
          aria-describedby={hintId}
          aria-invalid={tooLong || undefined}
          onChange={(e) => onChange(e.target.value)}
        />
        <p id={hintId} className={tooLong ? "field-error" : "muted small"}>
          Markdown. Raw HTML and images are left out of the preview.{" "}
          {draft.length.toLocaleString("en-US")} / {POSTMORTEM_MAX.toLocaleString("en-US")} characters.
        </p>
      </div>
      <div
        role="tabpanel"
        id={`${id}-preview`}
        aria-labelledby={`${id}-preview-tab`}
        hidden={tab !== "preview"}
        className="postmortem-preview"
        tabIndex={0}
      >
        {draft.trim() ? <Markdown source={draft} /> : <p className="muted">Nothing to preview.</p>}
      </div>
      <div className="form-actions">
        <AdminButton type="submit" className="button button-accent" disabled={busy || tooLong}>
          {busy ? "Saving…" : "Save postmortem"}
        </AdminButton>
        <button type="button" className="button button-secondary" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
      <OutcomeNote outcome={outcome} />
    </form>
  );
}
