import { Link } from "react-router-dom";
import { Card, Button, Badge } from "../components/ui";
import { PlatformAvatar } from "../components/ui/PlatformAvatar";
import { platformMeta } from "../lib/platforms";
import { useAccounts } from "../hooks/useAccounts";
import { useChannels } from "../hooks/useChannels";

/**
 * Dashboard.
 * A calm overview: what you can publish, and the fastest route to doing it.
 */
export function DashboardPage() {
  const { data: accounts, loading } = useAccounts();
  const firstAccountId = accounts?.[0]?.id ?? null;
  const { channels } = useChannels(firstAccountId);

  const totalChannels = accounts?.reduce((n, a) => n + a.channel_count, 0) ?? 0;

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
        <StatCard label="Publishing engine" value="Online" badge loading={loading} />
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

        {!loading && accounts && accounts.length === 0 && (
          <p className="publish__note">
            No accounts are configured yet. Add one in the backend to get started.
          </p>
        )}

        {!loading && accounts && accounts.length > 0 && (
          <div className="dash-accounts">
            {accounts.map((a) => (
              <div key={a.id} className="dash-account">
                <div className="dash-account__head">
                  <h4>{a.name}</h4>
                  <span className="dash-account__count">
                    {a.channel_count} channel{a.channel_count === 1 ? "" : "s"}
                  </span>
                </div>
                {a.id === firstAccountId ? (
                  <ul className="dash-channels">
                    {channels.map((c) => {
                      const meta = platformMeta(c.platform);
                      return (
                        <li key={c.id} className="dash-channel">
                          <PlatformAvatar meta={meta} size={26} />
                          <span>{meta.label}</span>
                        </li>
                      );
                    })}
                  </ul>
                ) : (
                  <p className="dash-account__muted">
                    {a.channel_count} channel{a.channel_count === 1 ? "" : "s"} connected
                  </p>
                )}
              </div>
            ))}
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
  badge = false,
}: {
  label: string;
  value: number | string;
  loading: boolean;
  badge?: boolean;
}) {
  return (
    <Card className="stat">
      <span className="stat__label">{label}</span>
      {loading ? (
        <div className="skeleton" style={{ height: 26, width: 56 }} />
      ) : badge ? (
        <Badge tone="success">
          <span className="stat__dot" aria-hidden="true" />
          {value}
        </Badge>
      ) : (
        <span className="stat__value">{value}</span>
      )}
    </Card>
  );
}