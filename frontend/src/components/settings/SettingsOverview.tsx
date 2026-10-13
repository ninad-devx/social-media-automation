/**
 * Settings > Overview.
 *
 * A single honest picture: what is configured, what is actually working, and
 * the next action when something is not. Every value comes from a real check or
 * a stored operation result. No secrets are ever rendered here.
 */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Button, Card, CardHeader, EmptyState } from "../../components/ui";
import { systemApi } from "../../services/integrations";
import type { SystemStatus } from "../../types/integration";
import { StatusBadge } from "../../components/settings/StatusBadge";

function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "unknown";
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function StatTile({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string | number;
  hint?: string;
  tone?: "success" | "danger" | "warning" | "neutral";
}) {
  return (
    <div className="stat-tile">
      <span className="stat-tile__label">{label}</span>
      <span className="stat-tile__value">
        {tone ? <Badge tone={tone}>{value}</Badge> : value}
      </span>
      {hint && <span className="stat-tile__hint">{hint}</span>}
    </div>
  );
}

export function SettingsOverview() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [probing, setProbing] = useState(false);

  const load = useCallback(async (probe: boolean) => {
    setLoading(true);
    setError(null);
    try {
      setStatus(await systemApi.status(probe));
    } catch (err) {
      // The message is kept, and rendered alongside the stale data below, so a
      // failed refresh can never be mistaken for a healthy one.
      setError(
        err instanceof Error
          ? `Could not refresh system status: ${err.message}`
          : "Could not read system status from the backend.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  const probe = async () => {
    setProbing(true);
    await load(true);
    setProbing(false);
  };

  if (loading && !status) return <p className="muted">Loading status…</p>;
  if (error && !status) return <EmptyState title="Backend unreachable" description={error} />;
  if (!status) return null;

  const integrationSeverity =
    status.integrations.needs_reauth > 0
      ? "danger"
      : status.integrations.total === 0
        ? "warning"
        : status.integrations.connected > 0
          ? "success"
          : "warning";

  return (
    <div className="stack">
      {/* Rendered even when stale data is on screen. Previously the error was
          only shown when there was no status at all, so a failed "Test n8n
          connection" silently left the previous green badges in place. */}
      {error && (
        <div className="banner banner--danger" role="status">
          {error}
          {status && " Showing the last known status, which may be out of date."}
        </div>
      )}

      {status.issues.length > 0 && (
        <Card>
          <CardHeader
            title="Needs attention"
            description="Each item below is based on a real check or a recorded failure."
          />
          <ul className="issue-list">
            {status.issues.map((issue, index) => (
              <li key={`${issue.area}-${index}`} className={`issue issue--${issue.severity}`}>
                <div className="issue__body">
                  <strong>{issue.message}</strong>
                  <span>{issue.action}</span>
                </div>
                <Badge
                  tone={
                    issue.severity === "error"
                      ? "danger"
                      : issue.severity === "warning"
                        ? "warning"
                        : "info"
                  }
                >
                  {issue.area}
                </Badge>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card>
        <CardHeader
          title="Overview"
          description="Live status of every moving part. Nothing here is inferred from a saved value alone."
          action={
            <Button size="sm" variant="ghost" onClick={probe} loading={probing}>
              Test n8n connection
            </Button>
          }
        />
        <div className="stat-grid">
          <StatTile
            label="Buffer integrations"
            value={
              status.integrations.total === 0
                ? "None"
                : `${status.integrations.connected}/${status.integrations.total} connected`
            }
            tone={integrationSeverity}
            hint={
              status.integrations.needs_reauth > 0
                ? `${status.integrations.needs_reauth} need re-authorising`
                : "Managed in Settings"
            }
          />
          <StatTile
            label="Accounts (brands)"
            value={status.counts.accounts_active}
            hint={`${status.counts.accounts} total`}
          />
          <StatTile
            label="Active channels"
            value={status.counts.channels_active}
            hint={`${status.integrations.channels_bound} linked to an integration`}
          />
          <StatTile
            label="Disconnected channels"
            value={status.integrations.channels_disconnected}
            tone={status.integrations.channels_disconnected > 0 ? "warning" : "success"}
            hint="Reported by Buffer"
          />
          <StatTile
            label="AI providers"
            value={status.ai.providers_total}
            tone={status.ai.ready ? "success" : "warning"}
            hint={status.ai.has_default ? "Default provider set" : "No default provider"}
          />
          <StatTile
            label="Credential encryption"
            value={status.encryption.configured ? "On" : "Off"}
            tone={status.encryption.configured ? "success" : "danger"}
            hint={
              status.encryption.key_source
                ? `Key: ${status.encryption.key_source}`
                : "No key configured"
            }
          />
        </div>
      </Card>

      <Card>
        <CardHeader title="System" description="Configuration as resolved by the backend." />
        <dl className="settings__facts">
          <div>
            <dt>Environment</dt>
            <dd>{status.environment}</dd>
          </div>
          <div>
            <dt>Database</dt>
            <dd>
              <StatusBadge status={status.database.ok ? "connected" : "connection_failed"} />{" "}
              {status.database.ok ? "reachable" : "unreachable"}
            </dd>
          </div>
          <div>
            <dt>Publishing engine</dt>
            <dd>
              {status.publish_engine === "direct"
                ? "Backend → Buffer (direct)"
                : "Backend → n8n workflow → Buffer"}
            </dd>
          </div>
          <div>
            <dt>n8n webhook</dt>
            <dd>
              {status.n8n.in_use ? (
                <>
                  <StatusBadge
                    status={
                      status.n8n.reachable === null
                        ? "not_checked"
                        : status.n8n.reachable
                          ? "connected"
                          : "connection_failed"
                    }
                  />{" "}
                  {status.n8n.reachable === null
                    ? "not probed yet"
                    : status.n8n.reachable
                      ? "reachable"
                      : "unreachable"}
                </>
              ) : (
                "Not used (direct engine)"
              )}
            </dd>
          </div>
          <div>
            <dt>Media storage</dt>
            <dd>
              {status.media.backend}
              {!status.media.publishable && (
                <>
                  {" "}
                  <Badge tone="warning">Buffer cannot download from this host</Badge>
                </>
              )}
            </dd>
          </div>
        </dl>
      </Card>

      {status.recent_failures.length > 0 && (
        <Card>
          <CardHeader
            title="Recent publishing failures"
            description="Last 7 days. The backend stores the code, never a stack trace or a token."
          />
          <ul className="issue-list">
            {status.recent_failures.map((failure) => (
              <li key={failure.request_id} className="issue issue--warning">
                <div className="issue__body">
                  <strong>
                    {failure.platform} · {failure.channel_id}
                  </strong>
                  <span>
                    {failure.error_code ?? "unknown"} · {relativeTime(failure.created_at)}
                  </span>
                </div>
                <Badge tone="danger">Failed</Badge>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card>
        <CardHeader title="Quick links" />
        <div className="button-row">
          <Link className="btn btn--secondary btn--sm" to="/settings/integrations">
            <span>Buffer integrations</span>
          </Link>
          <Link className="btn btn--secondary btn--sm" to="/settings/accounts">
            <span>Accounts &amp; channels</span>
          </Link>
          <Link className="btn btn--secondary btn--sm" to="/settings/ai">
            <span>AI providers</span>
          </Link>
          <Link className="btn btn--secondary btn--sm" to="/posts">
            <span>Publishing history</span>
          </Link>
        </div>
      </Card>
    </div>
  );
}
