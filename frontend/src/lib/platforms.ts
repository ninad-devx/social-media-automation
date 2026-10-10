/**
 * Platform identity.
 *
 * Everything platform-specific (label, colour, glyph, limits) lives here, so
 * selecting a different platform never requires touching a page component.
 */
export type PlatformKey =
  | "instagram"
  | "facebook"
  | "linkedin"
  | "twitter"
  | "threads"
  | "mastodon"
  | "youtube"
  | "bluesky"
  | "pinterest";

export interface PlatformMeta {
  key: PlatformKey;
  label: string;
  /** CSS custom property name holding the platform accent. */
  colorVar: string;
  /** Two-to-four character wordmark shown in the tinted avatar. */
  mark: string;
  /** Platforms that cannot accept a text-only post. */
  requiresMedia: boolean;
  /** Advisory only. The backend does not enforce this. */
  suggestedLimit: number | null;
  maxImages?: number;
}

const META: Record<string, PlatformMeta> = {
  instagram: {
    key: "instagram",
    label: "Instagram",
    colorVar: "--platform-instagram",
    mark: "IG",
    requiresMedia: true,
    suggestedLimit: 2200,
    maxImages: 10,
  },
  facebook: {
    key: "facebook",
    label: "Facebook",
    colorVar: "--platform-facebook",
    mark: "f",
    requiresMedia: false,
    suggestedLimit: 63206,
  },
  linkedin: {
    key: "linkedin",
    label: "LinkedIn",
    colorVar: "--platform-linkedin",
    mark: "in",
    requiresMedia: false,
    suggestedLimit: 3000,
  },
  twitter: {
    key: "twitter",
    label: "X",
    colorVar: "--platform-twitter",
    mark: "X",
    requiresMedia: false,
    suggestedLimit: 280,
  },
  threads: {
    key: "threads",
    label: "Threads",
    colorVar: "--platform-threads",
    mark: "@",
    requiresMedia: false,
    suggestedLimit: 500,
  },
  mastodon: {
    key: "mastodon",
    label: "Mastodon",
    colorVar: "--platform-mastodon",
    mark: "M",
    requiresMedia: false,
    suggestedLimit: 500,
  },
  youtube: {
    key: "youtube",
    label: "YouTube",
    colorVar: "--platform-youtube",
    mark: "YT",
    requiresMedia: true,
    suggestedLimit: 5000,
  },
  bluesky: {
    key: "bluesky",
    label: "Bluesky",
    colorVar: "--platform-bluesky",
    mark: "BS",
    requiresMedia: false,
    suggestedLimit: 300,
  },
  pinterest: {
    key: "pinterest",
    label: "Pinterest",
    colorVar: "--platform-pinterest",
    mark: "P",
    requiresMedia: true,
    suggestedLimit: 500,
  },
};

const FALLBACK: PlatformMeta = {
  key: "instagram",
  label: "Social",
  colorVar: "--platform-generic",
  mark: "S",
  requiresMedia: false,
  suggestedLimit: null,
};

export function platformMeta(platform: string | null | undefined): PlatformMeta {
  if (!platform) return FALLBACK;
  return META[platform.toLowerCase()] ?? { ...FALLBACK, label: platform };
}

/** Supported platform options, for the settings/filter UI. */
export const SUPPORTED_PLATFORMS = Object.values(META).map((p) => ({
  key: p.key,
  label: p.label,
}));