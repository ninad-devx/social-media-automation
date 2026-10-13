import { Card, EmptyState, Badge } from "../components/ui";
import { PlatformAvatar } from "../components/ui/PlatformAvatar";
import { useAccounts } from "../hooks/useAccounts";
import { useChannels } from "../hooks/useChannels";
import { platformMeta } from "../lib/platforms";
import { channelStatus } from "../types/channel";

/**
 * Accounts page.
 *
 * Purely configuration-driven: it renders whatever accounts and channels the
 * backend returns, so adding CEO / SortPi / Glaamora later requires no code
 * change. Buffer connections are managed outside this app.
 */
export function AccountsPage() {
  const { data: accounts, loading } = useAccounts();

  return (
    <div className="page">
      <header className="page__header">
        <div>
          <h1>Accounts</h1>
          <p className="page__subtitle">Brands and the channels connected to them.</p>
        </div>
      </header>

      {loading && !accounts && (
        <div className="accounts__grid">
          {[0, 1].map((i) => (
            <div key={i} className="skeleton" style={{ height: 190 }} />
          ))}
        </div>
      )}

      {accounts && accounts.length === 0 && (
        <Card>
          <EmptyState
            title="No accounts yet"
            description="Accounts are configured in the backend database. Add one there and it will appear here."
          />
        </Card>
      )}

      {accounts && accounts.length > 0 && (
        <div className="accounts__grid">
          {accounts.map((account) => (
            <AccountCard key={account.id} accountId={account.id} name={account.name} type={account.type} color={account.color} />
          ))}
        </div>
      )}

      <p className="page__footnote">
        Social account connections are managed in Buffer. This page shows application
        metadata only — no credentials are ever stored or displayed here.
      </p>
    </div>
  );
}

function AccountCard({
  accountId,
  name,
  type,
  color,
}: {
  accountId: string;
  name: string;
  type: string;
  color: string | null;
}) {
  const { channels, loading } = useChannels(accountId);

  const initials = name
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0])
    .join("")
    .toUpperCase();

  return (
    <Card className="account-card">
      <div className="account-card__head">
        <span
          className="account-card__mark"
          style={color ? ({ "--am-color": color } as React.CSSProperties) : undefined}
          aria-hidden="true"
        >
          {initials || name[0]?.toUpperCase()}
        </span>
        <div className="account-card__identity">
          <h3 className="account-card__name">{name}</h3>
          <span className="account-card__type">{type}</span>
        </div>
        <Badge tone={channels.length > 0 ? "success" : "warning"}>
          {channels.length > 0 ? "Active" : "No channels"}
        </Badge>
      </div>

      <div className="account-card__channels">
        <span className="selector__label">Connected channels</span>
        {loading && <div className="skeleton" style={{ height: 30, marginTop: 8 }} />}
        {!loading && channels.length === 0 && (
          <p className="account-card__empty">No channels configured yet.</p>
        )}
        {!loading && channels.length > 0 && (
          <ul className="channel-list">
            {channels.map((c) => {
              const meta = platformMeta(c.platform);
              return (
                <li key={c.id} className="channel-list__item">
                  <PlatformAvatar meta={meta} size={30} />
                  <div className="channel-list__body">
                    <span className="channel-list__name">{meta.label}</span>
                    {c.username && <span className="channel-list__handle">{c.username}</span>}
                  </div>
                  {(() => {
                    const health = channelStatus(c);
                    return (
                      <span
                        className="channel-list__status"
                        title={health.detail ?? undefined}
                      >
                        <span
                          className={`channel-list__dot is-${health.tone}`}
                          aria-hidden="true"
                        />
                        {health.label}
                      </span>
                    );
                  })()}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Card>
  );
}