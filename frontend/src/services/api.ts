import type { ApiErrorBody } from "../types/publishing";

const BASE = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");

/**
 * A normalized API failure. Carries the backend's stable error code so the UI
 * can react specifically, while only ever rendering `message` to the user.
 */
export class ApiError extends Error {
  readonly code: string;
  readonly field: string | null;
  readonly requestId: string;
  readonly status: number;
  readonly errors: ApiErrorBody["errors"];

  constructor(body: ApiErrorBody, status: number) {
    super(body?.error?.message ?? "Something went wrong. Please try again.");
    this.name = "ApiError";
    this.code = body?.error?.code ?? "INTERNAL_ERROR";
    this.field = body?.error?.field ?? null;
    this.requestId = body?.request_id ?? "";
    this.status = status;
    this.errors = body?.errors ?? [];
  }
}

export class NetworkError extends Error {
  readonly requestId = "";
  constructor(message = "We couldn't reach the server. Check your connection and try again.") {
    super(message);
    this.name = "NetworkError";
  }
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
  headers?: Record<string, string>;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, signal, headers = {} } = options;

  const finalHeaders: Record<string, string> = { Accept: "application/json", ...headers };
  if (body !== undefined) finalHeaders["Content-Type"] = "application/json";

  // Optional app-level key. Only needed for service-to-service style access;
  // an interactive user is identified by the session cookie set at login.
  const apiKey = import.meta.env.VITE_API_KEY;
  if (apiKey) finalHeaders["X-API-Key"] = apiKey;

  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      headers: finalHeaders,
      signal,
      // Sends the HttpOnly session cookie. Without this a cross-origin
      // request (the deployed frontend and API are different hosts) would
      // reach the backend anonymous and every call would 401.
      credentials: "include",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (err) {
    if ((err as Error)?.name === "AbortError") throw err;
    throw new NetworkError();
  }

  if (response.status === 204) return undefined as T;

  let payload: unknown = null;
  const text = await response.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    // 401 has a specific meaning now: the session is missing or expired, and
    // the app should show the login screen rather than a generic error.
    if (response.status === 401) {
      notifySessionExpired();
    }

    const defaultCode = codeForStatus(response.status);

    // FastAPI answers `{"detail": ...}` for HTTPException and for unmatched
    // routes, and the app registers handlers only for its own error types - so
    // there is no `error` key to read. The previous code treated any truthy
    // payload as our envelope, so every 404 surfaced as INTERNAL_ERROR with
    // "Something went wrong" and the specific fallback below was unreachable.
    const envelope = normalizeErrorPayload(payload);

    throw new ApiError(
      envelope ?? {
        success: false,
        request_id: response.headers.get("X-Request-Id") ?? "",
        error: {
          code: defaultCode,
          message:
            response.status === 401
              ? "Your session has ended. Please sign in again."
              : response.status === 404
                ? "We couldn't find what you were looking for."
                : detailMessage(payload) ??
                  `Something went wrong (HTTP ${response.status}). Please try again.`,
        },
      },
      response.status,
    );
  }

  return payload as T;
}

function codeForStatus(status: number): string {
  if (status === 401) return "UNAUTHORIZED";
  if (status === 403) return "FORBIDDEN";
  if (status === 404) return "NOT_FOUND";
  if (status === 409) return "CONFLICT";
  if (status === 422) return "INVALID_REQUEST";
  if (status === 429) return "RATE_LIMITED";
  if (status >= 500) return "INTERNAL_ERROR";
  return "REQUEST_FAILED";
}

/** Extract a message from whatever shape the server used. */
function detailMessage(payload: unknown): string | null {
  if (typeof payload === "string" && payload.trim()) return payload.trim().slice(0, 300);
  if (!payload || typeof payload !== "object") return null;
  const record = payload as Record<string, unknown>;
  if (typeof record.detail === "string" && record.detail.trim()) {
    return record.detail.trim().slice(0, 300);
  }
  if (record.detail && typeof record.detail === "object") {
    // FastAPI's 422 shape: { detail: [{ msg, loc, ... }] }
    const first = (record.detail as Array<Record<string, unknown>>)[0];
    if (first && typeof first.msg === "string") return first.msg.slice(0, 300);
  }
  return null;
}

/** True only when the payload really is our normalized error envelope. */
function normalizeErrorPayload(payload: unknown): ApiErrorBody | null {
  if (!payload || typeof payload !== "object") return null;
  const record = payload as Record<string, unknown>;
  const error = record.error as { code?: unknown; message?: unknown } | undefined;
  if (!error || typeof error !== "object") return null;
  if (typeof error.code !== "string" || typeof error.message !== "string") return null;
  return {
    success: false,
    request_id: typeof record.request_id === "string" ? record.request_id : "",
    error: { code: error.code, message: error.message, field: null },
    errors: Array.isArray(record.errors) ? (record.errors as ApiErrorBody["errors"]) : undefined,
  };
}

/**
 * Lets AuthProvider learn that a request came back 401 without every caller
 * having to handle it. Set by main.tsx's provider; a no-op when absent so the
 * module stays usable in isolation.
 */
let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn;
}

function notifySessionExpired() {
  try {
    onUnauthorized?.();
  } catch {
    /* a broken handler must never turn a 401 into a crash */
  }
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>(path, { signal }),
  post: <T>(
    path: string,
    body?: unknown,
    signal?: AbortSignal,
    headers?: Record<string, string>,
  ) => request<T>(path, { method: "POST", body, signal, headers }),
  put: <T>(path: string, body?: unknown, signal?: AbortSignal) =>
    request<T>(path, { method: "PUT", body, signal }),
  patch: <T>(path: string, body?: unknown, signal?: AbortSignal) =>
    request<T>(path, { method: "PATCH", body, signal }),
  del: <T>(path: string, signal?: AbortSignal) =>
    request<T>(path, { method: "DELETE", signal }),
};

export const API_BASE = BASE;