import { useEffect, useRef, useState } from "react";
import { api } from "../services/api";
import type { PlatformCapability, PostTypeKey } from "../lib/platforms";

/**
 * Platform capabilities, fetched once and shared.
 *
 * The backend owns every limit, required-media rule and post-type rule. The
 * frontend used to keep its own copy, which had drifted in three places and
 * disagreed with the backend about which platforms need media and how many
 * attachments they accept. This hook is the replacement: it renders from the
 * server's answer and never invents one.
 */

const FALLBACK: PlatformCapability = {
  platform: "instagram",
  label: "Social",
  known: false,
  requires_media: false,
  supports_image: true,
  supports_video: true,
  supports_carousel: false,
  max_assets: null,
  text_limit: null,
  default_post_type: "post",
  post_types: [],
};

interface CacheEntry {
  capability: PlatformCapability;
}

const cache = new Map<string, CacheEntry>();

export function usePlatformCapability(platform: string | null | undefined) {
  const key = (platform ?? "").toLowerCase();
  const [capability, setCapability] = useState<PlatformCapability | null>(
    key ? (cache.get(key)?.capability ?? null) : null,
  );
  const [loading, setLoading] = useState(false);
  const ticket = useRef(0);

  useEffect(() => {
    if (!key) {
      setCapability(null);
      return;
    }
    const cached = cache.get(key);
    if (cached) {
      setCapability(cached.capability);
      return;
    }

    const controller = new AbortController();
    const current = ++ticket.current;
    setLoading(true);

    api
      .get<PlatformCapability>(
        `/api/platforms/${encodeURIComponent(key)}`,
        controller.signal,
      )
      .then((result) => {
        if (current !== ticket.current) return;
        cache.set(key, { capability: result });
        setCapability(result);
      })
      .catch(() => {
        if (current !== ticket.current) return;
        // A failure must not invent rules. Report "unknown" and let the
        // composer stay neutral rather than assume permissive limits.
        setCapability({ ...FALLBACK, platform: key as never });
      })
      .finally(() => {
        if (current === ticket.current) setLoading(false);
      });

    return () => controller.abort();
  }, [key]);

  return { capability, loading };
}

export function useAllPlatforms() {
  const [platforms, setPlatforms] = useState<PlatformCapability[] | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    api
      .get<PlatformCapability[]>("/api/platforms", controller.signal)
      .then((rows) => {
        for (const row of rows) cache.set(row.platform, { capability: row });
        setPlatforms(rows);
      })
      .catch(() => setPlatforms([]));
    return () => controller.abort();
  }, []);

  return platforms;
}

/**
 * Pick the post type for a platform, defaulting to the platform default.
 *
 * Returns null when the platform has no known post types, which is the honest
 * answer: the composer then shows no type selector rather than offering
 * formats the platform may not accept.
 */
export function defaultPostType(capability: PlatformCapability | null): PostTypeKey | null {
  if (!capability || capability.post_types.length === 0) return null;
  const preferred = capability.post_types.find((t) => t.key === capability.default_post_type);
  return preferred ? preferred.key : capability.post_types[0].key;
}

export function postTypeCapability(
  capability: PlatformCapability | null,
  key: PostTypeKey | null,
) {
  if (!capability || !key) return null;
  return capability.post_types.find((t) => t.key === key) ?? null;
}