/**
 * Integration management client.
 *
 * Every call here is a UI action, not a code change: adding a Buffer account,
 * rotating a token or importing channels all happen through these endpoints.
 */
import { api } from "./api";
import type {
  ChannelSyncResult,
  ConnectionTestResult,
  DiscoveryResult,
  Integration,
  Organization,
  SystemStatus,
} from "../types/integration";

export interface IntegrationInput {
  display_name: string;
  api_key: string;
  provider?: string;
  organization_id?: string | null;
  enabled?: boolean;
  test_connection?: boolean;
}

export const integrationsApi = {
  list: (signal?: AbortSignal) => api.get<Integration[]>("/api/integrations", signal),

  create: (body: IntegrationInput) => api.post<Integration>("/api/integrations", body),

  update: (
    id: string,
    body: Partial<{ display_name: string; enabled: boolean; organization_id: string | null }>,
  ) => api.patch<Integration>(`/api/integrations/${encodeURIComponent(id)}`, body),

  /** Replaces the stored token and immediately re-tests the connection. */
  rotate: (id: string, apiKey: string) =>
    api.post<Integration>(`/api/integrations/${encodeURIComponent(id)}/rotate`, {
      api_key: apiKey,
    }),

  remove: (id: string) => api.del<void>(`/api/integrations/${encodeURIComponent(id)}`),

  /** Real provider check. The returned status is what was actually observed. */
  test: (id: string) =>
    api.post<ConnectionTestResult>(`/api/integrations/${encodeURIComponent(id)}/test`),

  organizations: (id: string) =>
    api.get<Organization[]>(`/api/integrations/${encodeURIComponent(id)}/organizations`),

  /** Live channel discovery against Buffer's API. */
  discover: (id: string) =>
    api.post<DiscoveryResult>(`/api/integrations/${encodeURIComponent(id)}/discover`),

  /** Imports the chosen channels into one brand. Idempotent. */
  sync: (id: string, body: { account_id: string; external_channel_ids: string[] }) =>
    api.post<ChannelSyncResult>(`/api/integrations/${encodeURIComponent(id)}/sync`, body),
};

export const systemApi = {
  /** `probeN8n` makes a real network call, so it is opt-in. */
  status: (probeN8n = false, signal?: AbortSignal) =>
    api.get<SystemStatus>(
      `/api/system/status${probeN8n ? "?n8n_probe=true" : ""}`,
      signal,
    ),
};

export const channelsAdminApi = {
  update: (id: string, body: Partial<{ display_name: string; description: string; active: boolean }>) =>
    api.patch<unknown>(`/api/channels/${encodeURIComponent(id)}`, body),

  /** Re-read one channel from its provider. */
  refresh: (id: string) => api.post<unknown>(`/api/channels/${encodeURIComponent(id)}/refresh`),

  /** Manual binding, for when automatic discovery is unavailable. */
  bind: (body: {
    account_id: string;
    external_channel_id: string;
    platform: string;
    display_name?: string;
    username?: string;
  }) => api.post<unknown>("/api/channels/bind", body),

  /**
   * Delete a channel outright.
   *
   * Disconnecting an integration only marks its channels disconnected, so the
   * rows survive by design. This removes one for good - used for leftovers
   * from a deleted integration, duplicates, and the legacy seed rows.
   */
  remove: (id: string) => api.del<void>(`/api/channels/${encodeURIComponent(id)}`),
};
