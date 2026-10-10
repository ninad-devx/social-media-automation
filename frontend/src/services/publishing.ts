import { api } from "./api";
import type { PublishLogPage, PublishRequest, PublishResponse } from "../types/publishing";

export const publishingApi = {
  /**
   * Sends an Idempotency-Key so a double-click or a retried request can never
   * publish the same post twice.
   */
  publish: (payload: PublishRequest, idempotencyKey: string) =>
    api.post<PublishResponse>("/api/publish", payload, { "Idempotency-Key": idempotencyKey }),

  history: (params: { limit?: number; offset?: number; accountId?: string } = {}, signal?: AbortSignal) => {
    const q = new URLSearchParams();
    if (params.limit) q.set("limit", String(params.limit));
    if (params.offset) q.set("offset", String(params.offset));
    if (params.accountId) q.set("account_id", params.accountId);
    const qs = q.toString();
    return api.get<PublishLogPage>(`/api/publish/history${qs ? `?${qs}` : ""}`, signal);
  },
};

export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `k-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}