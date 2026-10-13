/**
 * Frontend platform types.
 *
 * These mirror the backend capability model exactly. The values are NOT
 * hardcoded here any more: the app fetches them from `/api/platforms`, which is
 * the single source of truth. The constants below only exist as keys and as a
 * safe fallback for the first render before the fetch resolves.
 */

export type PlatformKey =
  | "instagram"
  | "facebook"
  | "linkedin"
  | "twitter"
  | "x"
  | "threads"
  | "mastodon"
  | "youtube"
  | "bluesky"
  | "pinterest";

export type PostTypeKey =
  | "post"
  | "video"
  | "reel"
  | "story"
  | "carousel"
  | "link"
  | "long_form";

export interface PostTypeCapability {
  key: PostTypeKey;
  label: string;
  min_assets: number | null;
  max_assets: number | null;
  allows_image: boolean;
  allows_video: boolean;
  requires_schedule: boolean;
}

export interface PlatformCapability {
  platform: PlatformKey;
  label: string;
  /** False for a platform this deployment has no rules for. */
  known: boolean;
  requires_media: boolean;
  supports_image: boolean;
  supports_video: boolean;
  supports_carousel: boolean;
  max_assets: number | null;
  text_limit: number | null;
  default_post_type: PostTypeKey;
  post_types: PostTypeCapability[];
}

/** Minimal shape needed to render a chip/avatar while the fetch is pending. */
export interface PlatformMeta {
  key: PlatformKey | string;
  label: string;
  colorVar: string;
  mark: string;
}

const MARKS: Record<string, { label: string; colorVar: string; mark: string }> = {
  instagram: { label: "Instagram", colorVar: "--platform-instagram", mark: "IG" },
  facebook: { label: "Facebook", colorVar: "--platform-facebook", mark: "f" },
  linkedin: { label: "LinkedIn", colorVar: "--platform-linkedin", mark: "in" },
  twitter: { label: "X", colorVar: "--platform-twitter", mark: "X" },
  x: { label: "X", colorVar: "--platform-twitter", mark: "X" },
  threads: { label: "Threads", colorVar: "--platform-threads", mark: "@" },
  mastodon: { label: "Mastodon", colorVar: "--platform-mastodon", mark: "M" },
  youtube: { label: "YouTube", colorVar: "--platform-youtube", mark: "YT" },
  bluesky: { label: "Bluesky", colorVar: "--platform-bluesky", mark: "BS" },
  pinterest: { label: "Pinterest", colorVar: "--platform-pinterest", mark: "P" },
};

const FALLBACK: PlatformMeta = {
  key: "unknown",
  label: "Social",
  colorVar: "--platform-generic",
  mark: "S",
};

/**
 * Display-only identity. Never used for rules.
 *
 * The audit found three copies of the platform table in this codebase which had
 * already drifted apart. Limits and capabilities now come from the backend via
 * `usePlatformCapability`; this only covers label/colour/mark.
 */
export function platformMeta(platform: string | null | undefined): PlatformMeta {
  if (!platform) return FALLBACK;
  const hit = MARKS[platform.toLowerCase()];
  if (!hit) return { ...FALLBACK, label: platform };
  return { key: platform.toLowerCase(), ...hit };
}

/** Every platform the backend reports, for filters and dropdowns. */
export const SUPPORTED_PLATFORMS: PlatformKey[] = Object.keys(MARKS) as PlatformKey[];

/** Post type keys, for the composer. */
export const POST_TYPE_KEYS: PostTypeKey[] = [
  "post",
  "carousel",
  "reel",
  "story",
  "video",
  "link",
  "long_form",
];