import { api, ApiError } from "./api";

export interface SessionUser {
  username: string;
  display_name: string | null;
  authenticated: boolean;
}

export interface AuthStatus {
  /** False when no operator is configured, so the login screen can explain why. */
  enabled: boolean;
  authenticated: boolean;
  username: string | null;
}

export const authApi = {
  /** Who am I? Safe to call anonymously; always resolves. */
  session: () => api.get<SessionUser>("/api/auth/session"),

  /** Is login possible on this deployment at all? */
  status: () => api.get<AuthStatus>("/api/auth/status"),

  /**
   * Exchange credentials for the HttpOnly session cookie.
   *
   * Returns the error message rather than throwing so the login form can
   * render it inline without a try/catch at every call site.
   */
  login: async (username: string, password: string): Promise<{ ok: boolean; message?: string }> => {
    try {
      // Form-encoded: the backend reads it with FastAPI's Form(...) dependency.
      const body = new URLSearchParams({ username, password });
      const response = await fetch(`${API_BASE_ORIGIN}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        credentials: "include",
        body,
      });
      if (response.ok) return { ok: true };

      let message = "We couldn't sign you in. Please try again.";
      try {
        const payload = (await response.json()) as { error?: { message?: string } };
        if (payload?.error?.message) message = payload.error.message;
      } catch {
        /* keep the generic message */
      }
      return { ok: false, message };
    } catch {
      return { ok: false, message: "We couldn't reach the server. Check your connection and try again." };
    }
  },

  logout: () => api.post<{ success: boolean }>("/api/auth/logout"),
};

/**
 * The raw base URL, kept separate from the ApiError-wrapping `request` helper
 * because login must distinguish a refusal from a transport failure.
 */
const API_BASE_ORIGIN = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);

export { ApiError };