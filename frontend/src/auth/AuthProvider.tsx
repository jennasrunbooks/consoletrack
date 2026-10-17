import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import * as api from "../api";
import type { User } from "../api";

interface AuthState {
  /** undefined while GET /auth/me is in flight; null when logged out. */
  user: User | null | undefined;
  /** Set when /auth/me failed for a reason other than "not logged in". */
  error: api.ApiError | null;
  /** True after a 401 on a logged-in call: the session ran out. */
  expired: boolean;
  login: (username: string, password: string) => Promise<User>;
  logout: () => Promise<void>;
  retry: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

// The session itself is the HttpOnly cookie; nothing here stores a token.
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null | undefined>(undefined);
  const [error, setError] = useState<api.ApiError | null>(null);
  const [expired, setExpired] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    api
      .getCurrentUser(controller.signal)
      .then((me) => {
        setError(null);
        setUser(me);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(err instanceof api.ApiError ? err : new api.ApiError(0, undefined));
        setUser(null);
      });
    return () => controller.abort();
  }, [attempt]);

  useEffect(() => {
    api.setUnauthorizedListener(() => {
      setUser((current) => {
        if (current) setExpired(true);
        return null;
      });
    });
    return () => api.setUnauthorizedListener(null);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const me = await api.login(username, password);
    setExpired(false);
    setError(null);
    setUser(me);
    return me;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch (err) {
      // Already logged out on the server: the result is the same.
      if (!(err instanceof api.ApiError && err.status === 401)) throw err;
    }
    setExpired(false);
    setUser(null);
  }, []);

  const retry = useCallback(() => {
    setUser(undefined);
    setAttempt((a) => a + 1);
  }, []);

  const value = useMemo(
    () => ({ user, error, expired, login, logout, retry }),
    [user, error, expired, login, logout, retry],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth outside AuthProvider");
  return value;
}

/** The logged-in user; only for components rendered behind the login gate. */
// eslint-disable-next-line react-refresh/only-export-components
export function useUser(): User {
  const { user } = useAuth();
  if (!user) throw new Error("useUser without a logged-in user");
  return user;
}
