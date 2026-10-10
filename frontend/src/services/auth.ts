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
   * Returns the signed-in operator on success, or an error message on failure,
   * so the caller never has to guess.
   *
   * Deliberately does NOT re-probe /auth/session afterwards. That second round
   * trip could fail for reasons unrelated to the login itself (the cookie not
   * yet visible to the browser, a transient network error) and would leave the
   * operator looking at the login screen again with no explanation. The login
   * response already carries the identity, so trust it.
   */
  login: async (
    username: string,
    password: string,
  ): Promise<{ ok: boolean; user?: SessionUser; message?: string }> => {
    try {
      const body = new URLSearchParams({ username, password });
      const response = await fetch(`${API_BASE_ORIGIN}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        credentials: "include",
        body,
      });

      const text = await response.text();
      let payload: {
        username?: string;
        display_name?: string | null;
        error?: { message?: string };
      } | null = null;
      try {
        payload = text ? JSON.parse(text) : null;
      } catch {
        payload = null;
      }

      if (response.ok) {
        return {
          ok: true,
          user: {
            username: payload?.username || username,
            display_name: payload?.display_name ?? null,
            authenticated: true,
          },
        };
      }

      return {
        ok: false,
        message: payload?.error?.message ?? "We couldn't sign you in. Please try again.",
      };
    } catch {
      return {
        ok: false,
        message: "We couldn't reach the server. Check your connection and try again.",
      };
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