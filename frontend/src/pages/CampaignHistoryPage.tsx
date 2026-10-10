import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Badge, Button, Card, EmptyState } from "../components/ui";
import { campaignsApi } from "../services/ai";
import { CampaignBoard } from "./AiAutomationPage";
import type { Campaign, CampaignPage } from "../types/ai";
import { formatDateTime } from "../lib/format";

const FILTERS = [
  { value: "", label: "All statuses" },
  { value: "draft", label: "Draft" },
  { value: "ready", label: "Ready" },
  { value: "published", label: "Published" },
  { value: "partially_failed", label: "Partially failed" },
];

const SOURCES = [
  { value: "", label: "Everything" },
  { value: "quick_publish", label: "Quick Publish" },
  { value: "ai_campaign", label: "AI campaigns" },
];

export function CampaignHistoryPage() {
  const [page, setPage] = useState<CampaignPage | null>(null);
  const [status, setStatus] = useState("");
  const [source, setSource] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      try {
        setPage(
          await campaignsApi.list(
            { limit: 50, status: status || undefined, source: source || undefined },
            signal
          )
        );
        setError(null);
      } catch (err) {
        if ((err as Error)?.name === "AbortError") return;
        setError((err as Error).message);
      } finally {
        setLoading(false);
      }
    },
    [status, source]
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const remove = async (id: string) => {
    if (!window.confirm("Delete this campaign and all of its drafts?")) return;
    try {
      await campaignsApi.remove(id);
      await load();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <div className="page">
      <header className="page__header">
        <div>
          <h1>Campaign history</h1>
          <p className="page__subtitle">Every campaign, its drafts, and what was published.</p>
        </div>
        <div className="row-actions">
          <select
            className="input"
            value={source}
            onChange={(e) => setSource(e.target.value)}
            aria-label="Filter by source"
          >
            {SOURCES.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
          <select
            className="input"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            aria-label="Filter by status"
          >
            {FILTERS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
          <Link className="btn btn--primary btn--md" to="/ai">
            <span>New campaign</span>
          </Link>
        </div>
      </header>

      {error && <Card className="notice notice--error">{error}</Card>}
      {loading && !page && <p className="muted">Loading campaigns…</p>}

      {page && page.items.length === 0 && (
        <Card>
          <EmptyState
            title="No campaigns yet"
            description="Create your first campaign in AI Automation."
            action={
              <Link className="btn btn--primary btn--md" to="/ai">
                <span>Open AI Automation</span>
              </Link>
            }
          />
        </Card>
      )}

      <ul className="stack">
        {page?.items.map((c) => (
          <li key={c.id} className="row-card">
            <div className="row-card__main">
              <div className="row-card__title">
                <strong>{c.name || c.topic}</strong>
                {c.source === "quick_publish" && <Badge tone="info">Quick Publish</Badge>}
                <Badge tone={tone(c.status)}>{label(c.status)}</Badge>
              </div>
              <p className="row-card__meta">{c.topic}</p>
              <p className="row-card__meta">
                {c.target_count} channel(s) · {c.generated_count} generated ·{" "}
                {c.published_count} published · {c.scheduled_count} scheduled
                {c.created_at ? ` · ${formatDateTime(c.created_at, "UTC")}` : ""}
              </p>
            </div>
            <div className="row-card__actions">
              <Link className="btn btn--secondary btn--sm" to={`/campaigns/${c.id}`}>
                <span>Open</span>
              </Link>
              <Button size="sm" variant="danger" onClick={() => remove(c.id)}>
                Delete
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function CampaignDetailPage() {
  const { campaignId = "" } = useParams();
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setCampaign(await campaignsApi.get(campaignId));
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }, [campaignId]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="page">
      <header className="page__header">
        <div>
          <h1>{campaign?.name || campaign?.topic || "Campaign"}</h1>
          <p className="page__subtitle">
            <Link to="/campaigns">← Back to history</Link>
          </p>
        </div>
      </header>

      {error && <Card className="notice notice--error">{error}</Card>}
      {!campaign && !error && <p className="muted">Loading campaign…</p>}

      {campaign && (
        <CampaignBoard
          campaign={campaign}
          onChanged={async () => {
            await load();
          }}
        />
      )}
    </div>
  );
}

function label(status: string) {
  const map: Record<string, string> = {
    draft: "Draft",
    ready: "Ready for review",
    partially_failed: "Partially failed",
    partially_published: "Partially published",
    published: "Published",
  };
  return map[status] ?? status;
}

function tone(status: string): "success" | "danger" | "warning" | "neutral" {
  if (status === "published" || status === "ready") return "success";
  if (status.includes("failed")) return "danger";
  if (status.includes("published")) return "warning";
  return "neutral";
}