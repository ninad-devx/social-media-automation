import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Card, Button, Badge } from "../components/ui";
import { PlatformAvatar } from "../components/ui/PlatformAvatar";
import { platformMeta } from "../lib/platforms";
import { useAccounts } from "../hooks/useAccounts";
import { useSystemStatus } from "../hooks/useSystemStatus";
import { channelsApi } from "../services/integrations";
import type { Channel } from "../types/channel";

/**
 * Dashboard.
 * A calm overview: what you can publish, and the fastest route to doing it.
 */
export function DashboardPage() {
  const { data: accounts, loading, error: accountsError } = useAccounts();
  const { data: system } = useSystemStatus();

  // The dashboard used to print a hardcoded green "Online" for the publishing
  // engine regardless of what was actually configured or reachable.
  const engineStatus = !system
    ? "Unknown"
    : system.publish_engine === "direct"
      ? "Direct to Buffer"
      : system.n8n.reachable === null
        ? "Not checked"
        : system.n8n.reachable
          ? "Online"
          : "Unreachable";
  const engineTone: "success" | "neutral" | "danger" = !system
    ? "neutral"
    : engineStatus === "Online" || engineStatus === "Direct to Buffer"
      ? "success"
      : engineStatus === "Not checked"
        ? "neutral"
        : "danger";
  const totalChannels = accounts?.reduce((n, a) => n + a.channel_count, 0) ?? 0;

  // Every brand's channels, not just the first.
  //
  // The dashboard used to call useChannels once, for accounts[0], and rendered
  // a bare "N channels connected" for every other brand. With more than one
  // brand configured that is most of the workspace reduced to a number: you
  // could not see, from the dashboard, which channels a brand actually had.
  const [channelsByAccount, setChannelsByAccount] = useState<Record<string, Channel[]>>({});
  const accountIds = (accounts ?? []).map((a) => a.id).join(",");

  useEffect(() => {
    if (!accountIds) {
      setChannelsByAccount({});
      return;
    }
    const controller = new AbortController();
    const ids = accountIds.split(",");
    let cancelled = false;

    Promise.all(
      ids.map((id) =>
        channelsApi
          .listForAccount(id, false, controller.signal)
          .then((list) => [id, list] as const)
          .catch(() => [id, [] as Channel[]] as const),
      ),
    ).then((pairs) => {
      if (cancelled) return;
      setChannelsByAccount(Object.fromEntries(pairs));
    });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [accountIds]);

  return (
    <div className="page">
      <header className="page__header">
        <div>
          <h1>Dashboard</h1>
          <p className="page__subtitle">Your publishing workspace at a glance.</p>
        </div>
        <Link to="/">
          <Button variant="primary">Quick Publish</Button>
        </Link>
      </header>

      <div className="stats">
        <StatCard label="Accounts" value={accounts?.length ?? 0} loading={loading} />
        <StatCard label="Connected channels" value={totalChannels} loading={loading} />
        <StatCard
          label="Publishing engine"
          value={engineStatus}
          tone={engineTone}
          loading={loading}
        />
      </div>

      <Card>
        <div className="card__header">
          <div className="card__header-text">
            <h3 className="card__title">Start publishing</h3>
            <p className="card__description">
              Choose an account and channel, write your post, and send it out.
            </p>
          </div>
        </div>

        {loading && <div className="skeleton" style={{ height: 120 }} />}

        {accountsError && (
          <p className="notice notice--error" role="alert">
            Could not load your accounts: {accountsError}
          </p>
        )}

        {!loading && accounts && accounts.length === 0 && (
          <p className="publish__note">
            No accounts are configured yet. Add one in the backend to get started.
          </p>
        )}

        {!loading && accounts && accounts.length > 0 && (
          <div className="dash-accounts">
            {accounts.map((a) => {
              const brandChannels = channelsByAccount[a.id] ?? [];
              return (
                <div key={a.id} className="dash-account">
                  <div className="dash-account__head">
                    <h4>{a.name}</h4>
                    <span className="dash-account__count">
                      {a.channel_count} channel{a.channel_count === 1 ? "" : "s"}
                    </span>
                  </div>
                  {brandChannels.length > 0 ? (
                    <ul className="dash-channels">
                      {brandChannels.map((c) => {
                        const meta = platformMeta(c.platform);
                        return (
                          <li key={c.id} className="dash-channel">
                            <PlatformAvatar meta={meta} size={26} />
                            {/* Profile and brand, not just the platform name:
                                three Instagram accounts are otherwise
                                indistinguishable on this screen. */}
                            <span>{c.display_name || c.username || meta.label}</span>
                            <span className="dash-channel__platform">{meta.label}</span>
                          </li>
                        );
                      })}
                    </ul>
                  ) : (
                    <p className="dash-account__muted">
                      {a.channel_count > 0
                        ? `${a.channel_count} channel${a.channel_count === 1 ? "" : "s"} connected`
                        : "No channels connected yet"}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}

function StatCard({
  label,
  value,
  loading,
  tone,
}: {
  label: string;
  value: number | string;
  loading: boolean;
  /** Tints the value. The engine card used to always render green. */
  tone?: 'success' | 'neutral' | 'danger';
}) {
  return (
    <Card className="stat">
      <span className="stat__label">{label}</span>
      {loading ? (
        <div className="skeleton" style={{ height: 26, width: 56 }} />
      ) : tone ? (
        <Badge tone={tone}>
          <span className="stat__dot" aria-hidden="true" />
          {value}
        </Badge>
      ) : (
        <span className="stat__value">{value}</span>
      )}
    </Card>
  );
}