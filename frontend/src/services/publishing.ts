/**
 * Publishing client.
 */
import { api } from "./api";
import type { PublishLogPage, PublishRequest, PublishResponse } from "../types/publishing";

export interface HistoryParams {
  limit?: number;
  offset?: number;
  accountId?: string;
  channelId?: string;
  status?: string;
  platform?: string;
}

/**
 * Query-string builder.
 *
 * Uses `!== undefined` rather than a truthiness check: `offset: 0` is the first
 * valid page and the old `if (params.offset)` silently dropped it.
 */
function qs(params: Record<string, string | number | undefined>): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
  }
  return parts.length ? `?${parts.join("&")}` : "";
}

export interface DuplicateGroup {
  account_id: string;
  channel_id: string;
  platform: string;
  copies: number;
  row_ids: number[];
  request_ids: string[];
  statuses: string[];
}

export interface DuplicateReport {
  generated_at: string;
  total_rows: number;
  duplicate_groups: DuplicateGroup[];
  redundant_rows: number;
  note: string;
}

/**
 * A per-attempt idempotency key.
 *
 * Reused across retries of the *same* attempt so the backend can replay the
 * stored response instead of publishing twice, and rotated by `reset()` once an
 * attempt is done. The backend treats a `failed` key as retryable and a
 * `completed` key as final, so a transient provider error no longer wedges the
 * post permanently.
 */
export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `ui-${crypto.randomUUID()}`;
  }
  return `ui-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

export const publishingApi = {
  /**
   * Publish or schedule.
   *
   * `idempotencyKey` is sent as the `Idempotency-Key` header. This used to be
   * passed as the abort signal by mistake, so the key never reached the API and
   * the double-submit protection the header exists for was inert.
   */
  publish: (
    body: PublishRequest,
    idempotencyKey?: string,
    signal?: AbortSignal,
  ) =>
    api.post<PublishResponse>(
      "/api/publish",
      body,
      signal,
      idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
    ),

  history: (params: HistoryParams = {}, signal?: AbortSignal) => {
    const query = qs({
      limit: params.limit,
      offset: params.offset,
      account_id: params.accountId,
      channel_id: params.channelId,
      status: params.status,
      platform: params.platform,
    });
    return api.get<PublishLogPage>(`/api/publish/history${query}`, signal);
  },

  /**
   * Pre-existing duplicate history rows. Read-only: the backend reports, it
   * never reconciles, because each row records a real attempt.
   */
  duplicateHistory: (signal?: AbortSignal) =>
    api.get<DuplicateReport>("/api/publish/history/duplicates", signal),

  /**
   * Remove one history row.
   *
   * Local only. This deletes this app's record of an attempt; it does not
   * delete, edit or cancel the post on Buffer. The backend sends nothing to the
   * publishing engine from this call.
   */
  deleteHistoryEntry: (id: number) => api.del<void>(`/api/publish/history/${id}`),

  /**
   * Bulk-remove history.
   *
   * The backend refuses an unscoped call, so at least one filter is required.
   * That is why the parameters have no "all" default.
   */
  clearHistory: (params: Omit<HistoryParams, "limit" | "offset">) => {
    const query = qs({
      account_id: params.accountId,
      channel_id: params.channelId,
      status: params.status,
      platform: params.platform,
    });
    return api.del<{ deleted: number }>(`/api/publish/history${query}`);
  },
};