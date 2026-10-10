import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { setUnauthorizedHandler } from "../services/api";
import { authApi, type SessionUser } from "../services/auth";

interface AuthState {
  /** Null until the initial session probe has finished. */
  user: SessionUser | null;
  loading: boolean;
  /** False when the deployment has no operator configured. */
  enabled: boolean;
  signIn: (username: string, password: string) => Promise<{ ok: boolean; message?: string }>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<SessionUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [enabled, setEnabled] = useState(true);

  const probe = useCallback(async () => {
    try {
      // Two calls because they answer different questions: "can anyone log in
      // here" (deployment state) and "is this browser signed in" (session).
      const [status, session] = await Promise.all([authApi.status(), authApi.session()]);
      setEnabled(status.enabled);
      setUser(session.authenticated ? session : null);
    } catch {
      // A backend that is unreachable leaves the app signed out rather than
      // stuck on a spinner.
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void probe();
  }, [probe]);

  // Any 401 from anywhere in the app drops straight back to the login screen
  // instead of leaving a broken half-signed-in UI on screen.
  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    return () => setUnauthorizedHandler(null);
  }, []);

  const signIn = useCallback(async (username: string, password: string) => {
    const result = await authApi.login(username, password);
    if (result.ok) {
      const session = await authApi.session();
      setUser(session.authenticated ? session : null);
      setEnabled(true);
    }
    return result;
  }, []);

  const signOut = useCallback(async () => {
    try {
      await authApi.logout();
    } catch {
      // Clearing local state is what matters; a failed request must not
      // strand the operator in the app.
    }
    setUser(null);
  }, []);

  const value = useMemo<AuthState>(
    () => ({ user, loading, enabled, signIn, signOut }),
    [user, loading, enabled, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}