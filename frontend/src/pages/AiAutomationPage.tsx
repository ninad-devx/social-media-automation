import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useNavigate } from "react-router-dom";

import { Badge, Button, Card, CardHeader, EmptyState, Field } from "../components/ui";

import { Select } from "../components/ui/Select";

import { accountsApi } from "../services/accounts";

import { campaignsApi, providersApi } from "../services/ai";

import { useAccounts } from "../hooks/useAccounts";

import type { AiProvider, Campaign, CampaignPost, PostActionResult } from "../types/ai";

import { channelCaption, channelStatus, type Channel } from "../types/channel";

import { platformMeta } from "../lib/platforms";

import {
  mediaCapNote,
  PostPreviewCard,
  PreviewGrid,
  type MediaCaps,
} from "../components/ai/PostPreviewCard";
import { MediaPicker } from "../components/quick-publish/MediaPicker";
import type { MediaItem } from "../types/publishing";

/** Consecutive failed polls before we stop and tell the operator. */
const MAX_POLL_ERRORS = 5;

export function AiAutomationPage() {

  const navigate = useNavigate();

  const { data: accounts } = useAccounts();

  const [channels, setChannels] = useState<Channel[]>([]);

  // accountId -> selected channel ids. A campaign needs one post per channel,

  // so this must be a list, not a single value.

  const [selected, setSelected] = useState<Record<string, string[]>>({});

  /** Brands the operator wants to generate for. Defaults to the first brand. */
  const [brandIds, setBrandIds] = useState<string[]>([]);

  const [providers, setProviders] = useState<AiProvider[]>([]);

  const [providerId, setProviderId] = useState("");
  const [providerError, setProviderError] = useState<string | null>(null);

  const [topic, setTopic] = useState("");

  const [context, setContext] = useState("");

  const [objective, setObjective] = useState("");

  const [tone, setTone] = useState("");

  const [cta, setCTA] = useState("");

  const [language, setLanguage] = useState("en");
  const [generateImages, setGenerateImages] = useState(false);

  const [campaign, setCampaign] = useState<Campaign | null>(null);

  const [creating, setCreating] = useState(false);

  const [generating, setGenerating] = useState(false);

  const [error, setError] = useState<string | null>(null);

  const pollRef = useRef<number | null>(null);
  const pollErrors = useRef(0);

  /* ---- loading ---- */

  useEffect(() => {

    providersApi

      .list()

      .then((list) => {

        setProviders(list);

        const def = list.find((p) => p.is_default) ?? list[0];

        if (def) setProviderId(def.id);

      })

      .catch((err) => {
        // Previously the error was discarded and the Model picker simply
        // vanished, with no explanation and no fallback shown.
        setProviders([]);
        setProviderError(
          err instanceof Error
            ? `Could not load AI providers: ${err.message}`
            : "Could not load AI providers.",
        );
      });

  }, []);

  // H10: the previous version hardcoded accounts[0].id, so a campaign could
  // only ever be generated for the first brand no matter what the operator
  // picked. Channels are now loaded per selected brand.
  useEffect(() => {
    if (!accounts?.length) {
      setChannels([]);
      return;
    }

    // Default to the first brand until the operator chooses others.
    setBrandIds((prev) => (prev.length ? prev : [accounts[0].id]));

    const controller = new AbortController();
    Promise.all(
      (brandIds.length ? brandIds : [accounts[0].id]).map((id) =>
        accountsApi.channels(id, controller.signal),
      ),
    )
      .then((lists) => {
        const merged: Channel[] = [];
        const seen = new Set<string>();
        for (const list of lists) {
          for (const channel of list) {
            if (seen.has(channel.id)) continue;
            seen.add(channel.id);
            merged.push(channel);
          }
        }
        setChannels(merged);
      })
      .catch(() => setChannels([]));

    return () => controller.abort();
  }, [accounts, brandIds]);

  const targets = useMemo(

    () =>

      Object.entries(selected).flatMap(([accountId, channelIds]) =>

        channelIds.map((channelId) => ({ account_id: accountId, channel_id: channelId }))

      ),

    [selected]

  );

  /* ---- generation polling ---- */

  const stopPolling = useCallback(() => {

    if (pollRef.current != null) {

      window.clearInterval(pollRef.current);

      pollRef.current = null;

    }

  }, []);

  const refresh = useCallback(

    async (id: string) => {

      try {

        const fresh = await campaignsApi.get(id);

        setCampaign(fresh);

        return fresh;

      } catch (err) {
        // A single transient failure used to resolve to null, which the poll
        // loop read as "nothing is generating" and stopped polling for good,
        // leaving a half-generated board with no error shown.
        pollErrors.current += 1;
        if (pollErrors.current >= MAX_POLL_ERRORS) {
          stopPolling();
          setGenerating(false);
          setError(
            (err as Error)?.message ??
              "Lost contact with the server while generating. Please start generation again.",
          );
        }
        return undefined;
      } finally {
        pollErrors.current = 0;
      }

    },

    []

  );

  const startGeneration = useCallback(

    async (id: string) => {

      setGenerating(true);

      try {

        await campaignsApi.generate(id, providerId || undefined);

        stopPolling();

        pollRef.current = window.setInterval(async () => {

          const fresh = await refresh(id);

          // `null` means the request failed; that is not the same as "finished".
          if (fresh === null) return;

          const busy = (fresh?.posts ?? []).some(
            (p) => p.generation_status === "pending" || p.generation_status === "generating",
          );

          if (!busy) {

            stopPolling();

            setGenerating(false);

          }

        }, 1500);

      } catch (err) {

        setError((err as Error).message);

        setGenerating(false);

      }

    },

    [providerId, refresh, stopPolling]

  );

  useEffect(() => stopPolling, [stopPolling]);

  /* ---- actions ---- */

  const createAndGenerate = async () => {

    setError(null);

    if (!topic.trim()) {

      setError("Give the campaign a topic first.");

      return;

    }

    if (!targets.length) {

      setError("Choose at least one channel.");

      return;

    }

    setCreating(true);

    try {

      const created = await campaignsApi.create({

        topic: topic.trim(),

        objective: objective.trim() || null,

        context: context.trim() || null,

        tone_override: tone.trim() || null,

        cta: cta.trim() || null,

language,
        generate_images: generateImages,
        targets,
      });

      setCampaign(created);

      await startGeneration(created.id);

    } catch (err) {

      setError((err as Error).message);

    } finally {

      setCreating(false);

    }

  };

  const onPostChanged = async (post?: CampaignPost) => {

    if (!campaign) return;

    if (post) {
      setCampaign({

        ...campaign,

        posts: campaign.posts.map((p) => (p.id === post.id ? post : p)),

      });
    }

    // Always refetch exactly once. Called with no argument after a bulk
    // operation, so a 25-channel campaign issues one request rather than 25.
    await refresh(campaign.id);

  };

  if (!accounts?.length) {

    return (

      <div className="page">

        <EmptyState

          title="No accounts connected"

          description="Connect a social account before creating campaigns."

        />

      </div>

    );

  }

  return (

    <div className="page">

      <header className="page__header">

        <div>

          <h1>AI Automation</h1>

          <p className="page__subtitle">

            Generate drafts for every channel at once, review them, then publish what you approve.

          </p>

        </div>

        {campaign && (

          <Button onClick={() => navigate(`/campaigns/${campaign.id}`)}>Open in history</Button>

        )}

      </header>

      {error && <Card className="notice notice--error">{error}</Card>}

      <div className="ai-layout">

        <Card className="ai-layout__aside">

          <CardHeader

            title="Campaign brief"

            description="Tell the model what this campaign is about."

          />

          <Field label="Topic" htmlFor="topic" hint="The main thing this campaign is about.">

            <textarea className="textarea"
              id="topic"

              rows={3}

              value={topic}

              onChange={(e) => setTopic(e.target.value)}

              placeholder="Launch of our new scheduling tool"

            />

          </Field>

          <Field

            label="Verified context"

            htmlFor="context"

            hint="Facts the model may use. Anything not here must not be invented."

          >

            <textarea className="textarea"
              id="context"

              rows={4}

              value={context}

              onChange={(e) => setContext(e.target.value)}

              placeholder="Launched in March. Works with Google Calendar and Outlook."

            />

          </Field>

          <Field label="Objective" htmlFor="objective">

            <input className="input"
              id="objective"

              value={objective}

              onChange={(e) => setObjective(e.target.value)}

              placeholder="Drive sign-ups"

            />

          </Field>

          <Field label="Tone adjustment" htmlFor="tone" hint="Applied on top of the brand voice.">

            <input className="input" id="tone" value={tone} onChange={(e) => setTone(e.target.value)} />

          </Field>

          <Field label="Call to action" htmlFor="cta">

            <input className="input" id="cta" value={cta} onChange={(e) => setCTA(e.target.value)} />

          </Field>

          <label className="checkbox" style={{ marginBottom: "var(--s-3)" }}>
            <input
              type="checkbox"
              checked={generateImages}
              onChange={(e) => setGenerateImages(e.target.checked)}
            />
            <span>Also generate an image for every channel</span>
          </label>

          <Field label="Language" htmlFor="language">

            <Select

              id="language"

              value={language}

              onChange={setLanguage}

              options={[

                { value: "en", label: "English" },

                { value: "bn", label: "Bangla" },

                { value: "es", label: "Spanish" },

                { value: "fr", label: "French" },

                { value: "de", label: "German" },

                { value: "ar", label: "Arabic" },

              ]}

            />

          </Field>

          <Field label="Publish to" htmlFor="channel-picker">

            <ChannelPicker

              channels={channels}

              value={brandIds.flatMap((id) => selected[id] ?? [])}

              onChange={(channelIds) => {
                // H10: the selection is stored per chosen brand, so a campaign
                // can span brands instead of only ever the first one.
                const first = brandIds[0];
                setSelected(
                  Object.fromEntries(
                    brandIds.map((id) => [
                      id,
                      id === first ? channelIds : (selected[id] ?? []),
                    ]),
                  ),
                );
              }}

            />

          </Field>
          {providerError && (
            <p className="notice notice--error" role="status">
              {providerError}
            </p>
          )}


          {providers.length > 0 && (

            <Field label="Model" htmlFor="provider-picker">

              <Select

                id="provider-picker"

                value={providerId}

                onChange={setProviderId}

                options={providers.map((p) => ({

                  value: p.id,

                  label: `${p.display_name}${p.model ? ` (${p.model})` : ""}`,

                }))}

              />

            </Field>

          )}

          <Button
            variant="primary"

            fullWidth

            loading={creating || generating}

            onClick={createAndGenerate}

          >

            {generating ? "Generating..." : campaign ? "Start a new campaign" : "Generate drafts"}

          </Button>

          <p className="muted">

            Nothing is published automatically. Every draft is reviewed here first.

          </p>

        </Card>

        <div className="ai-layout__main">

          {!campaign && (

            <Card>

              <EmptyState

                title="No drafts yet"

                description="Fill in the brief on the left and generate drafts for your channels."

              />

            </Card>

          )}

          {campaign && (

            <CampaignBoard campaign={campaign} onChanged={onPostChanged} />

          )}

        </div>

      </div>

    </div>

  );

}

/* ------------------------------------------------------------------------ */

function ChannelPicker({

  channels,

  value,

  onChange,

}: {

  channels: Channel[];

  value: string[];

  onChange: (ids: string[]) => void;

}) {

  const toggle = (id: string) =>

    onChange(value.includes(id) ? value.filter((c) => c !== id) : [...value, id]);

  if (!channels.length) return <p className="muted">No channels available.</p>;

  return (

    <ul className="checkbox-list">

      {channels.map((c) => {
        // H3: the picker used to render a plain checkbox for every channel and
        // read none of the backend's eligibility fields, so a locked or
        // disconnected profile could be selected for generation and then fail
        // at publish time. The verdict and its reason are both shown now.
        const health = channelStatus(c);
        const blocked = !c.publishable;
        return (
          <li key={c.id}>

            <label className="checkbox" title={health.detail ?? undefined}>

              <input

                type="checkbox"

                checked={value.includes(c.id)}

                disabled={blocked}

                onChange={() => toggle(c.id)}

              />

              <span title={channelCaption(c)}>{channelCaption(c)}</span>

              <Badge tone={health.tone}>{health.label}</Badge>

            </label>

            {blocked && health.detail && (

              <p className="muted small">{health.detail}</p>

            )}

          </li>
        );
      })}

    </ul>

  );

}

/* ------------------------------------------------------------------------ */

export function CampaignBoard({

  campaign,

  onChanged,

}: {

  campaign: Campaign;

  onChanged: (post?: CampaignPost) => void | Promise<void>;

}) {

  // H9: keyed by campaign id so navigating from one campaign to another can
  // never carry the previous campaign's post ids into a publish.
  const [selection, setSelection] = useState<{ campaignId: string; ids: string[] }>({
    campaignId: campaign.id,
    ids: [],
  });
  const selected = selection.campaignId === campaign.id ? selection.ids : [];

  const [results, setResults] = useState<PostActionResult[]>([]);

  const [busy, setBusy] = useState(false);
  /** Bulk action failures, shown in the board rather than a native alert. */
  const [boardError, setBoardError] = useState<string | null>(null);

  const publishable = campaign.posts.filter(

    (p) => p.publishing_status === "draft" && p.effective_content.trim().length > 0

  );

  const toggle = (id: string) =>

    setSelection(({ ids }) => ({
      campaignId: campaign.id,
      ids: ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id],
    }));

  const bulkPublish = async () => {

    if (!selected.length) return;

    setBusy(true);

    setResults([]);

    try {

      const result = await campaignsApi.bulkPublish(selected);

      setResults(result.results);

      // Refresh every row so statuses reflect what Buffer actually accepted.

      // H8: this used to call onChanged once per post in the campaign, so
    // publishing 3 of 25 fired 25 identical GETs plus 25 writes from the same
    // stale closure. One refetch is enough and is race-free.
    await onChanged();

      setSelection({ campaignId: campaign.id, ids: [] });

    } catch (err) {

      // A native modal blocked the UI and the message vanished on dismissal.
      setBoardError((err as Error).message);

    } finally {

      setBusy(false);

    }

  };

  return (

    <Card>

      <CardHeader

        title={campaign.name || campaign.topic}

        description={`${campaign.generated_count} generated · ${campaign.failed_count} failed · ${campaign.published_count} published`}

        action={<Badge tone={statusTone(campaign.status)}>{statusLabel(campaign.status)}</Badge>}

      />

      {boardError && (

        <p className="notice notice--error" role="status">

          {boardError}

        </p>

      )}


      {results.length > 0 && (

        <div className="notice notice--info">

          <strong>Bulk publish finished.</strong>

          <ul>

            {results.map((r) => (

              <li key={r.post_id}>

                {r.ok ? "Published" : `Failed: ${r.error_message ?? r.error_code}`}

              </li>

            ))}

          </ul>

        </div>

      )}

      {publishable.length > 0 && (
        <div className="row-actions">
          <Button
            variant="primary"
            loading={busy}

            disabled={!selected.length}

            onClick={bulkPublish}

          >

            Publish {selected.length > 0 ? `${selected.length} ` : ""}selected

          </Button>

          <Button
            onClick={() =>
              setSelection({ campaignId: campaign.id, ids: publishable.map((p) => p.id) })
            }

            disabled={selected.length === publishable.length}

          >

            Select all

          </Button>

          <Button
            onClick={() => setSelection({ campaignId: campaign.id, ids: [] })}
            disabled={!selected.length}
          >

            Clear

          </Button>

        </div>

      )}

      <PreviewGrid>

        {campaign.posts.map((post) => (

          <PostCard

            key={post.id}

            post={post}

            selectable={publishable.some((p) => p.id === post.id)}

            selected={selected.includes(post.id)}

            onToggle={() => toggle(post.id)}

            onChanged={onChanged}

          />

        ))}

      </PreviewGrid>

    </Card>

  );

}

/* ------------------------------------------------------------------------ */

function PostCard({

  post,

  selectable,

  selected,

  onToggle,

  onChanged,

}: {

  post: CampaignPost;

  selectable: boolean;

  selected: boolean;

  onToggle: () => void;

  onChanged: (post?: CampaignPost) => void | Promise<void>;

}) {

  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(post.effective_content);
  const [addingMedia, setAddingMedia] = useState(false);
  const [media, setMedia] = useState<MediaItem[]>(
    post.media.map((m) => ({
      type: (m.type as MediaItem["type"]) ?? "image",
      url: m.url ?? "",
      alt_text: m.alt_text ?? "",
    }))
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => setText(post.effective_content), [post.effective_content]);

  // M12: the editor text is resynced but the media list was not, so after a
  // regenerate the card still showed - and saved back - the previous URLs.
  useEffect(() => {
    setMedia(
      post.media.map((m) => ({
        type: (m.type === "video" ? "video" : "image") as MediaItem["type"],
        url: m.url ?? "",
        alt_text: m.alt_text ?? null,
      })),
    );
  }, [post.media]);

  const locked = post.publishing_status === "published" || post.publishing_status === "scheduled";
  const meta = platformMeta(post.platform);
  const [caps, setCaps] = useState<MediaCaps | null>(null);

  // What this platform will accept, so the picker cannot offer a carousel to
  // a network that has none.
  useEffect(() => {
    const controller = new AbortController();
    campaignsApi
      .mediaCapabilities(post.platform, controller.signal)
      .then(setCaps)
      .catch(() => setCaps(null));
    return () => controller.abort();
  }, [post.platform]);

  const run = async (key: string, fn: () => Promise<CampaignPost | void>) => {

    setBusy(key);

    setMessage(null);

    try {

      const result = await fn();

      if (result) await onChanged(result);

    } catch (err) {

      setMessage((err as Error).message);

    } finally {

      setBusy(null);

    }

  };

  const publish = async () => {

    setBusy("publish");

    setMessage(null);

    try {

      const result = await campaignsApi.publishPost(post.id);

      if (!result.ok) setMessage(result.error_message ?? result.error_code ?? "Publish failed.");

      await onChanged(post);

    } catch (err) {

      setMessage((err as Error).message);

    } finally {

      setBusy(null);

    }

  };

  const mediaRequired = Boolean(caps?.requires_media);

  const canPublish =

    !locked &&

    post.publishing_status === "draft" &&

    post.effective_content.trim().length > 0 &&

    (!mediaRequired || post.media.length > 0);

  // Why the Publish button is not there.
  //
  // The previous version explained only the media case. Every other reason - a
  // channel Buffer has locked, a post already published, blank copy - removed
  // the button and said nothing at all, which reads as a broken screen rather
  // than a deliberate block.
  const blockedReason = ((): string | null => {
    if (canPublish) return null;
    if (locked) return `${meta.label} has this channel locked, so Buffer cannot post to it.`;
    if (post.publishing_status === "published")
      return "This post already went out. Its copy is the record of what was sent.";
    if (post.publishing_status === "scheduled")
      return "This post is already scheduled. Check the Posts page for its status.";
    if (post.publishing_status === "failed")
      return "The last attempt failed. Edit it and publish again to try once more.";
    if (!post.effective_content.trim()) return "This post has no text yet.";
    if (mediaRequired && post.media.length === 0)
      return `${meta.label} needs an image before it can publish.`;
    return "This post cannot be published right now.";
  })();

  return (

    <PostPreviewCard post={post} requiresMedia={Boolean(caps?.requires_media)}>

      {selectable && (

        <label className="preview-card__select">

          <input type="checkbox" checked={selected} onChange={onToggle} />

          <span>Select for bulk publish</span>

        </label>

      )}

      {post.error_message && <p className="notice notice--error">{post.error_message}</p>}

      {message && <p className="notice notice--error">{message}</p>}

      {post.validation_warnings.length > 0 && (

        <ul className="notice notice--warn">

          {post.validation_warnings.map((w) => (

            <li key={w}>{w}</li>

          ))}

        </ul>

      )}

      {editing ? (
        <div className="post-card__editor">
          <textarea className="textarea" rows={6} value={text} onChange={(e) => setText(e.target.value)} />
          <div className="row-actions">
            <Button
              variant="primary"
              size="sm"
              loading={busy === "save"}
              onClick={() =>
                run("save", async () => {
                  const updated = await campaignsApi.updatePost(post.id, {
                    content: text,
                    media: media.filter((m) => m.url),
                  });
                  setEditing(false);
                  return updated;
                })
              }
            >
              Save
            </Button>
            <Button
              size="sm"
              onClick={() => {
                setText(post.effective_content);
                setAddingMedia(false);
                setEditing(false);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : null}
      {editing && addingMedia && (
        <div className="post-card__media-editor">
          <p className="muted">{mediaCapNote(caps)}</p>
          <MediaPicker
            media={media}
            onChange={setMedia}
            requiresMedia={Boolean(caps?.requires_media)}
            maxItems={caps?.max_assets ?? 1}
          />
        </div>
      )}

      <div className="row-actions">

        {locked ? (

          <span className="muted">Locked — already sent.</span>

        ) : (

          <>

        {!editing && (
          <Button size="sm" onClick={() => setEditing(true)}>
            Edit
          </Button>
        )}

        {!locked && (
          <Button
            size="sm"
            onClick={() => {
              setEditing(true);
              setAddingMedia((open) => !open);
            }}
            title={mediaCapNote(caps)}
          >
            {media.length ? "Change media" : "Add media"}
          </Button>
        )}

            <Button
              size="sm"

              loading={busy === "regen"}

              disabled={post.is_edited}

              title={

                post.is_edited

                  ? "You edited this post. Regenerate over the edit to replace it."

                  : "Generate a fresh draft"

              }

              onClick={() => run("regen", () => campaignsApi.regeneratePost(post.id, false))}

            >

              Regenerate

            </Button>

            {post.is_edited && (

              <>

                <Button
                  size="sm"

                  loading={busy === "restore"}

                  onClick={() => run("restore", () => campaignsApi.restorePost(post.id))}

                >

                  Undo edit

                </Button>

                <Button
                  size="sm"

                  loading={busy === "discard"}

                  onClick={() => run("discard", () => campaignsApi.discardEdits(post.id))}

                >

                  Discard edits

                </Button>

                <Button
                  size="sm"

                  loading={busy === "regenover"}

                  title="Replace your edit with a fresh draft"

                  onClick={() => run("regenover", () => campaignsApi.regeneratePost(post.id, true))}

                >

                  Regenerate over edit

                </Button>

              </>

            )}

            <Button
              size="sm"

              loading={busy === "image"}

              title="Create an image from this post's caption"

              onClick={() => run("image", () => campaignsApi.generateImage(post.id))}

            >

              {post.media.length ? "New image" : "Generate image"}

            </Button>

            {canPublish ? (

              <Button size="sm" variant="primary" loading={busy === "publish"} onClick={publish}>

                Publish

              </Button>

            ) : (

              blockedReason && (

                <span className="muted">{blockedReason}</span>

              )

            )}

          </>

        )}

      </div>

    </PostPreviewCard>

  );

}

function statusLabel(status: string) {

  const map: Record<string, string> = {

    draft: "Draft",

    ready: "Ready for review",

    partially_failed: "Partially failed",

    partially_published: "Partially published",

    published: "Published",

  };

  return map[status] ?? status;

}

function statusTone(status: string): "success" | "danger" | "warning" | "info" | "neutral" {

  if (status === "published" || status === "ready") return "success";

  if (status.includes("failed")) return "danger";

  if (status.includes("published")) return "warning";

  return "neutral";

}

