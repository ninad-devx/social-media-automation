/**
 * Status pills.
 *
 * Every label here is derived from a value the backend actually verified. The
 * audit found AccountsPage rendering a hardcoded green "Connected" for every
 * channel regardless of state, and ChannelStatusBadge disagreeing with the
 * Quick Publish dropdown about "queue paused" for the same channel. Both now
 * go through the single `channelStatus` helper in types/channel.ts.
 */
import { Badge } from "../ui";
import { channelStatus, type Channel } from "../../types/channel";
import type { IntegrationStatus } from "../../types/integration";

const LABELS: Record<string, string> = {
  not_configured: "Not configured",
  // Distinct from `not_configured`: this means "never checked", not "misconfigured".
  not_checked: "Not checked yet",
  connected: "Connected",
  connection_failed: "Connection failed",
  disabled: "Disabled",
  reauth_required: "Re-authorisation required",
  sync_required: "Synchronisation required",
};

const TONES: Record<string, "neutral" | "success" | "danger" | "warning" | "info"> = {
  not_configured: "neutral",
  not_checked: "neutral",
  connected: "success",
  connection_failed: "danger",
  disabled: "neutral",
  reauth_required: "warning",
  sync_required: "info",
};

export function statusLabel(status: string | undefined | null): string {
  if (!status) return "Unknown";
  return LABELS[status] ?? status;
}

export function StatusBadge({ status }: { status: IntegrationStatus | string | undefined | null }) {
  return <Badge tone={TONES[status ?? ""] ?? "neutral"}>{statusLabel(status)}</Badge>;
}

/**
 * A channel's connection state.
 *
 * Uses the same helper as every other channel surface, so one channel reads
 * identically wherever it appears. `not_checked` is deliberately distinct from
 * `not_configured`: "we have not looked yet" is not "this is misconfigured".
 */
export function ChannelStatusBadge({ channel }: { channel: Channel }) {
  const verdict = channelStatus(channel);
  const title = verdict.detail ?? undefined;
  return (
    <span title={title}>
      <Badge tone={verdict.tone}>{verdict.label}</Badge>
    </span>
  );
}

/** Compact variant for dense tables. */
export function ChannelStatusDot({ channel }: { channel: Channel }) {
  const verdict = channelStatus(channel);
  return (
    <span className="channel-status-dot" title={verdict.detail ?? verdict.label}>
      <span className={`channel-status-dot__mark is-${verdict.tone}`} aria-hidden="true" />
      {verdict.label}
    </span>
  );
}