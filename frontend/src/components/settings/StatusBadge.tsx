/**
 * Integration status pill.
 *
 * Statuses come from real probes and stored operation results - never from
 * "is a text field non-empty".
 */
import { Badge } from "../ui";
import type { IntegrationStatus } from "../../types/integration";

const LABELS: Record<string, string> = {
  not_configured: "Not configured",
  connected: "Connected",
  connection_failed: "Connection failed",
  disabled: "Disabled",
  reauth_required: "Re-authorisation required",
  sync_required: "Synchronisation required",
};

const TONES: Record<string, "neutral" | "success" | "danger" | "warning" | "info"> = {
  not_configured: "neutral",
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

/** Same vocabulary, applied to a channel's connection state. */
export function ChannelStatusBadge({
  connectionStatus,
  locked,
  queuePaused,
}: {
  connectionStatus?: string | null;
  locked?: boolean;
  queuePaused?: boolean;
}) {
  if (locked) return <Badge tone="danger">Locked in Buffer</Badge>;
  if (connectionStatus === "reauth_required") {
    return <Badge tone="warning">Disconnected</Badge>;
  }
  if (connectionStatus === "connected" && queuePaused) {
    return <Badge tone="info">Queue paused</Badge>;
  }
  if (connectionStatus === "connected") return <Badge tone="success">Connected</Badge>;
  return <Badge tone="neutral">Not linked</Badge>;
}
