import type { PlatformMeta } from "../../lib/platforms";

/**
 * Platform avatar. Renders the platform wordmark on a tinted disc so each
 * channel is instantly recognisable without shipping brand logos.
 */
export function PlatformAvatar({
  meta,
  size = 32,
}: {
  meta: PlatformMeta;
  size?: number;
}) {
  return (
    <span
      className="platform-avatar"
      style={
        {
          "--pa-size": `${size}px`,
          "--pa-color": `var(${meta.colorVar})`,
        } as React.CSSProperties
      }
      aria-hidden="true"
    >
      {meta.mark}
    </span>
  );
}