import { Select, type SelectOption } from "../ui/Select";
import type { PlatformCapability, PostTypeKey } from "../../lib/platforms";

/**
 * Post type picker.
 *
 * The options come entirely from the backend capability model. There is no
 * hardcoded list here, and nothing is offered that the selected channel's
 * platform cannot actually publish - the audit found the whole concept missing,
 * with Instagram and Facebook pinned to a plain feed `post` no matter what was
 * attached.
 *
 * When the platform reports no post types at all, no selector is rendered rather
 * than a guessed default.
 */
export function PostTypePicker({
  capability,
  value,
  onChange,
  disabled,
  error,
}: {
  capability: PlatformCapability | null;
  value: string | null;
  onChange: (next: string | null) => void;
  disabled?: boolean;
  error?: string | null;
}) {
  if (!capability) return null;

  // A single option needs no choice, so it is not rendered as a control.
  if (capability.post_types.length <= 1) return null;

  const options: SelectOption<string>[] = capability.post_types.map((t) => ({
    value: t.key,
    label: t.label,
    sublabel: describeType(capability, t) ?? undefined,
  }));

  const selected = value ?? capability.default_post_type;

  return (
    <div className="publish__post-type">
      <span className="selector__label" id="post-type-label">
        Format
      </span>
      <Select
        value={selected}
        options={options}
        onChange={(next) => onChange(next as PostTypeKey)}
        disabled={disabled}
        aria-labelledby="post-type-label"
      />
      {error && (
        <p className="field__error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

/** A one-line, accurate summary of what this format needs. */
function describeType(capability: PlatformCapability, type: PlatformCapability["post_types"][number]) {
  const parts: string[] = [];
  if (type.requires_schedule) parts.push("schedule only");
  if (type.allows_video) parts.push("video");
  else if (type.allows_image) parts.push("image");
  if (type.max_assets === 1) parts.push("1 attachment");
  else if (type.max_assets && type.max_assets > 1) parts.push(`up to ${type.max_assets}`);
  // The format's own minimum, regardless of the platform flag.
  //
  // This used to be `capability.requires_media && type.min_assets`, so on
  // Facebook - where the platform flag is false because a feed post can be
  // text-only - a Reel and a Story advertised nothing at all, even though both
  // reject a post with no video.
  if (type.min_assets) {
    parts.push(
      type.allows_image && type.allows_video
        ? `needs ${type.min_assets}+`
        : `needs ${type.min_assets}+ video`,
    );
  } else if (capability.requires_media) {
    parts.push("media required");
  }
  return parts.join(" · ") || null;
}