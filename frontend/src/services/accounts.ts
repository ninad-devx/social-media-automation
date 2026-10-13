import { api } from "./api";
import type { Account, AccountDetail, Channel } from "../types/account";

export const accountsApi = {
  list: (signal?: AbortSignal) => api.get<Account[]>("/api/accounts", signal),
  /** Every brand, including empty/inactive ones, for the Settings screens. */
  manage: (signal?: AbortSignal) => api.get<Account[]>("/api/accounts/manage", signal),
  detail: (id: string, includeInactive = false, signal?: AbortSignal) =>
    api.get<AccountDetail>(
      `/api/accounts/${encodeURIComponent(id)}${includeInactive ? "?include_inactive=true" : ""}`,
      signal,
    ),
  channels: (accountId: string, signal?: AbortSignal) =>
    api.get<Channel[]>(`/api/accounts/${encodeURIComponent(accountId)}/channels`, signal),
  create: (body: { name: string; type?: string; description?: string; color?: string }) =>
    api.post<AccountDetail>("/api/accounts", body),
  update: (id: string, body: Partial<{ name: string; description: string; active: boolean; color: string }>) =>
    api.patch<AccountDetail>(`/api/accounts/${encodeURIComponent(id)}`, body),
  remove: (id: string) => api.del<void>(`/api/accounts/${encodeURIComponent(id)}`),
  health: (signal?: AbortSignal) => api.get<Record<string, unknown>>("/api/health", signal),
};

export const healthApi = {
  check: (signal?: AbortSignal) => api.get<Record<string, unknown>>("/api/health", signal),
};