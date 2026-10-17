import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api";

export interface Resource<T> {
  data: T | undefined;
  error: ApiError | undefined;
  /** True until the first response (or error) for the current key. */
  loading: boolean;
  reload: () => void;
}

/**
 * Loads `fetcher` whenever `key` changes, aborting the previous request, and every `pollMs` if
 * set. Earlier data stays visible while a reload is in flight, so tables don't flash.
 */
export function useResource<T>(
  key: string,
  fetcher: (signal: AbortSignal) => Promise<T>,
  { pollMs }: { pollMs?: number } = {},
): Resource<T> {
  const [state, setState] = useState<{ key: string; data?: T; error?: ApiError; done: boolean }>({
    key,
    done: false,
  });
  const [tick, setTick] = useState(0);
  const fetcherRef = useRef(fetcher);
  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  useEffect(() => {
    const controller = new AbortController();
    fetcherRef
      .current(controller.signal)
      .then((data) => setState({ key, data, done: true }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        const error = err instanceof ApiError ? err : new ApiError(0, { code: "client_error", message: String(err) });
        setState((prev) => ({ key, data: prev.data, error, done: true }));
      });
    return () => controller.abort();
  }, [key, tick]);

  useEffect(() => {
    if (!pollMs) return;
    const id = setInterval(() => setTick((t) => t + 1), pollMs);
    return () => clearInterval(id);
  }, [pollMs]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return {
    data: state.data,
    error: state.error,
    loading: !(state.done && state.key === key),
    reload,
  };
}
