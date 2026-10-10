import { DateTime } from "luxon";

/** Format an ISO instant in a specific IANA zone. */
export function formatInZone(iso: string | null, zone: string, pattern: string): string {
  if (!iso) return "—";
  const dt = DateTime.fromISO(iso, { zone: "utc" }).setZone(zone);
  return dt.isValid ? dt.toFormat(pattern) : "—";
}

export function formatDateTime(iso: string | null, zone = "UTC"): string {
  return formatInZone(iso, zone, "d LLL yyyy, h:mm a");
}

/** "in 4 minutes" / "2 hours ago" style relative time. */
export function relativeTime(iso: string | null): string {
  if (!iso) return "";
  const dt = DateTime.fromISO(iso);
  if (!dt.isValid) return "";
  return dt.toRelative({ style: "short" }) ?? "";
}

export function truncate(text: string, max = 140): string {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length <= max ? clean : `${clean.slice(0, max - 1)}…`;
}

export function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}