import { useEffect, useId, useRef, useState, type InputHTMLAttributes } from "react";

export const DEBOUNCE_MS = 300;

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange"> & {
  /** The committed value (from the URL). */
  value: string;
  onCommit: (value: string) => void;
  normalize?: (value: string) => string;
  /** An error message, or null if the value can be committed. */
  validate?: (value: string) => string | null;
};

/** A text input that commits after typing pauses, and follows outside changes (Clear filters). */
export function DebouncedInput({ value, onCommit, normalize = (v) => v.trim(), validate, ...props }: Props) {
  const [draft, setDraft] = useState(value);
  const [committed, setCommitted] = useState(value);
  const errorId = useId();
  // Callers pass a fresh callback each render; a re-render mustn't restart the timer.
  const onCommitRef = useRef(onCommit);
  useEffect(() => {
    onCommitRef.current = onCommit;
  });

  // The URL changed from elsewhere: show the new value. (Adjusting state during render, not
  // in an effect, so there is no flash of the old value.)
  if (value !== committed) {
    setCommitted(value);
    if (normalize(draft) !== value) setDraft(value);
  }

  const next = normalize(draft);
  const error = validate?.(next) ?? null;

  useEffect(() => {
    if (next === committed || error) return;
    const id = setTimeout(() => onCommitRef.current(next), DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [next, committed, error]);

  return (
    <>
      <input
        {...props}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
      />
      {error && (
        <span id={errorId} className="field-error" role="alert">
          {error}
        </span>
      )}
    </>
  );
}
