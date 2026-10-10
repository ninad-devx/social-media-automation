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

  // Optional app-level key. Empty in local development.
  const apiKey = import.meta.env.VITE_API_KEY;
  if (apiKey) finalHeaders["X-API-Key"] = apiKey;

  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      headers: finalHeaders,
      signal,
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
    const fallback: ApiErrorBody = {
      success: false,
      request_id: response.headers.get("X-Request-Id") ?? "",
      error: {
        code: response.status === 404 ? "NOT_FOUND" : "INTERNAL_ERROR",
        message: "Something went wrong. Please try again.",
      },
    };
    throw new ApiError((payload as ApiErrorBody) ?? fallback, response.status);
  }

  return payload as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>(path, { signal }),
  post: <T>(path: string, body?: unknown, headers?: Record<string, string>) =>
    request<T>(path, { method: "POST", body, headers }),
  put: <T>(path: string, body?: unknown, signal?: AbortSignal) =>
    request<T>(path, { method: "PUT", body, signal }),
  patch: <T>(path: string, body?: unknown, signal?: AbortSignal) =>
    request<T>(path, { method: "PATCH", body, signal }),
  del: <T>(path: string, signal?: AbortSignal) =>
    request<T>(path, { method: "DELETE", signal }),
};

export const API_BASE = BASE;