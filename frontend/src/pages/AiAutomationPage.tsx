import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useNavigate } from "react-router-dom";

import { Badge, Button, Card, CardHeader, EmptyState, Field } from "../components/ui";

import { Select } from "../components/ui/Select";

import { accountsApi } from "../services/accounts";

import { campaignsApi, providersApi } from "../services/ai";

import { useAccounts } from "../hooks/useAccounts";

import type { AiProvider, Campaign, CampaignPost, PostActionResult } from "../types/ai";

import type { Channel } from "../types/channel";

import { platformMeta } from "../lib/platforms";


import {
  mediaCapNote,
  PostPreviewCard,
  PreviewGrid,
  type MediaCaps,
} from "../components/ai/PostPreviewCard";
import { MediaPicker } from "../components/quick-publish/MediaPicker";
import type { MediaItem } from "../types/publishing";



export function AiAutomationPage() {

  const navigate = useNavigate();

  const { data: accounts } = useAccounts();



  const [channels, setChannels] = useState<Channel[]>([]);

  // accountId -> selected channel ids. A campaign needs one post per channel,

  // so this must be a list, not a single value.

  const [selected, setSelected] = useState<Record<string, string[]>>({});

  const [providers, setProviders] = useState<AiProvider[]>([]);

  const [providerId, setProviderId] = useState("");



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



  /* ---- loading ---- */

  useEffect(() => {

    providersApi

      .list()

      .then((list) => {

        setProviders(list);

        const def = list.find((p) => p.is_default) ?? list[0];

        if (def) setProviderId(def.id);

      })

      .catch(() => setProviders([]));

  }, []);



  useEffect(() => {

    if (!accounts?.length) return;

    const controller = new AbortController();

    accountsApi

      .channels(accounts[0].id, controller.signal)

      .then(setChannels)

      .catch(() => setChannels([]));

    return () => controller.abort();

  }, [accounts]);



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

      } catch {

        return null;

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

          const busy = fresh?.posts.some(

            (p) => p.generation_status === "pending" || p.generation_status === "generating"

          );

          if (!busy) {

            stopPolling();

            setGenerating(false);

          }

        }, 1200);

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



  const onPostChanged = async (post: CampaignPost) => {

    if (!campaign) return;

    setCampaign({

      ...campaign,

      posts: campaign.posts.map((p) => (p.id === post.id ? post : p)),

    });

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

              value={selected[accounts[0].id] ?? []}

              onChange={(channelIds) =>

                setSelected({ ...selected, [accounts[0].id]: channelIds })

              }

            />

          </Field>



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

            {generating ? "GeneratingÃ¢â‚¬Â¦" : campaign ? "Start a new campaign" : "Generate drafts"}

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

      {channels.map((c) => (

        <li key={c.id}>

          <label className="checkbox">

            <input
              type="checkbox"

              checked={value.includes(c.id)}

              onChange={() => toggle(c.id)}

            />

            <span>{c.display_name ?? c.id}</span>

          </label>

        </li>

      ))}

    </ul>

  );

}



/* ------------------------------------------------------------------------ */

export function CampaignBoard({

  campaign,

  onChanged,

}: {

  campaign: Campaign;

  onChanged: (post: CampaignPost) => void | Promise<void>;

}) {

  const [selected, setSelected] = useState<string[]>([]);

  const [results, setResults] = useState<PostActionResult[]>([]);

  const [busy, setBusy] = useState(false);



  const publishable = campaign.posts.filter(

    (p) => p.publishing_status === "draft" && p.effective_content.trim().length > 0

  );



  const toggle = (id: string) =>

    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));



  const bulkPublish = async () => {

    if (!selected.length) return;

    setBusy(true);

    setResults([]);

    try {

      const result = await campaignsApi.bulkPublish(selected);

      setResults(result.results);

      // Refresh every row so statuses reflect what Buffer actually accepted.

      await Promise.all(campaign.posts.map((p) => onChanged(p)));

      setSelected([]);

    } catch (err) {

      window.alert((err as Error).message);

    } finally {

      setBusy(false);

    }

  };



  return (

    <Card>

      <CardHeader

        title={campaign.name || campaign.topic}

        description={`${campaign.generated_count} generated Ã‚Â· ${campaign.failed_count} failed Ã‚Â· ${campaign.published_count} published`}

        action={<Badge tone={statusTone(campaign.status)}>{statusLabel(campaign.status)}</Badge>}

      />



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

            Publish {selected.length || ""} selected

          </Button>

          <Button
            onClick={() => setSelected(publishable.map((p) => p.id))}

            disabled={selected.length === publishable.length}

          >

            Select all

          </Button>

          <Button onClick={() => setSelected([])} disabled={!selected.length}>

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

  onChanged: (post: CampaignPost) => void | Promise<void>;

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



  const canPublish =

    !locked &&

    post.publishing_status === "draft" &&

    post.effective_content.trim().length > 0 &&

    (!meta.requiresMedia || post.media.length > 0);



  return (

    <PostPreviewCard post={post}>

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

          <span className="muted">Locked â€” already sent.</span>

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

              meta.requiresMedia && post.media.length === 0 && (

                <span className="muted">

                  {meta.label} needs an image before it can publish.

                </span>

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

