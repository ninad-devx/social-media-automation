/**
 * Integration / settings types.
 *
 * Note what is deliberately absent: every Buffer credential field. The
 * backend returns only `credential_hint` (a masked tail), so a token can never
 * be rendered, logged or persisted by the browser.
 */

export type IntegrationStatus =
  | "not_configured"
  | "connected"
  | "connection_failed"
  | "disabled"
  | "reauth_required"
  | "sync_required";

export interface Organization {
  id: string;
  name: string;
}

export interface Integration {
  id: string;
  provider: string;
  display_name: string;
  enabled: boolean;
  status: IntegrationStatus;
  credential_hint: string | null;
  credential_stored: boolean;
  external_organization_id: string | null;
  external_organization_name: string | null;
  available_organizations: Organization[];
  channel_count: number;
  active_channel_count: number;
  last_tested_at: string | null;
  last_sync_at: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ConnectionTestResult {
  ok: boolean;
  status: IntegrationStatus;
  integration_id: string;
  organization_id: string | null;
  organization_name: string | null;
  message: string;
  checked_at: string;
}

export interface DiscoveredChannel {
  external_channel_id: string;
  service: string;
  name: string;
  display_name: string | null;
  descriptor: string | null;
  channel_type: string | null;
  avatar: string | null;
  external_link: string | null;
  timezone: string | null;
  disconnected: boolean;
  locked: boolean;
  queue_paused: boolean;
  supported: boolean;
  local_channel_id: string | null;
  local_account_id: string | null;
  already_imported: boolean;
}

export interface DiscoveryResult {
  integration_id: string;
  organization_id: string;
  organization_name: string | null;
  channels: DiscoveredChannel[];
  total: number;
  importable: number;
}

export interface ChannelSyncResult {
  integration_id: string;
  account_id: string;
  created: number;
  updated: number;
  skipped: number;
  channel_ids: string[];
  synced_at: string;
}

export interface SystemIssue {
  severity: "error" | "warning" | "info";
  area: string;
  message: string;
  action: string;
}

export interface SystemStatus {
  environment: string;
  publish_engine: "n8n" | "direct";
  backend: Record<string, unknown>;
  database: { ok: boolean; error?: string };
  n8n: {
    configured: boolean;
    webhook_url: string;
    reachable: boolean | null;
    in_use: boolean;
  };
  encryption: {
    configured: boolean;
    using_legacy_ai_key: boolean;
    key_source: string | null;
  };
  media: {
    backend: string;
    public_base_url_configured: boolean;
    publishable: boolean;
  };
  integrations: {
    total: number;
    connected: number;
    disabled: number;
    needs_reauth: number;
    channels_bound: number;
    channels_unbound: number;
    channels_disconnected: number;
    by_status: Record<string, number>;
  };
  ai: {
    providers_total: number;
    providers_enabled: number;
    has_default: boolean;
    ready: boolean;
  };
  counts: {
    accounts: number;
    accounts_active: number;
    channels: number;
    channels_active: number;
  };
  recent_failures: Array<{
    request_id: string;
    account_id: string;
    channel_id: string;
    platform: string;
    error_code: string | null;
    created_at: string | null;
  }>;
  issues: SystemIssue[];
}
