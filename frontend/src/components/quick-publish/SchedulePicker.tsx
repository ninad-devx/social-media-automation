import { DateTime } from "luxon";
import { Select, type SelectOption } from "../ui/Select";

const COMMON_TIMEZONES = [
  "Asia/Dhaka",
  "Asia/Kolkata",
  "Asia/Dubai",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Asia/Seoul",
  "Asia/Shanghai",
  "Europe/London",
  "Europe/Dublin",
  "Europe/Paris",
  "Europe/Berlin",
  "Europe/Moscow",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "America/Toronto",
  "Australia/Sydney",
  "Pacific/Auckland",
  "UTC",
];

/**
 * Schedule picker.
 *
 * All timezone maths lives here via Luxon: the browser's own zone is never
 * assumed, and no timestamp is ever produced by string concatenation. The
 * value handed to the parent is a naive local datetime plus an explicit
 * IANA timezone - exactly the contract the publishing engine expects.
 *
 * Scheduling architecture note: nothing here runs a job. A scheduled post is
 * handed to Buffer with a `dueAt` and Buffer publishes it. There is no
 * in-application queue, worker or cron, and the history rows for scheduled posts
 * are created when the request is accepted - not when Buffer later fires them.
 */
export function SchedulePicker({
  date,
  time,
  timezone,
  onChange,
  error,
  disabled,
  minDate,
}: {
  date: string;
  time: string;
  timezone: string;
  onChange: (next: { date: string; time: string; timezone: string }) => void;
  error?: string | null;
  disabled?: boolean;
  minDate: string;
}) {
  const zoneOptions: SelectOption<string>[] = COMMON_TIMEZONES.map((tz) => ({
    value: tz,
    label: tz.replace(/_/g, " "),
    sublabel: offsetLabel(tz),
  }));

  // Offer the browser's zone too, if it is not already listed.
  const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (browserZone && !COMMON_TIMEZONES.includes(browserZone)) {
    zoneOptions.push({
      value: browserZone,
      label: browserZone.replace(/_/g, " "),
      sublabel: "Your device",
    });
  }

  const preview = describe(date, time, timezone);

  return (
    <div className="schedule">
      <div className="schedule__row">
        <div className="schedule__field">
          <label className="selector__label" htmlFor="schedule-date">
            Date
          </label>
          <input
            id="schedule-date"
            className="input"
            type="date"
            value={date}
            min={minDate}
            disabled={disabled}
            onChange={(e) => onChange({ date: e.target.value, time, timezone })}
          />
        </div>

        <div className="schedule__field schedule__field--time">
          <label className="selector__label" htmlFor="schedule-time">
            Time
          </label>
          <input
            id="schedule-time"
            className="input"
            type="time"
            value={time}
            disabled={disabled}
            onChange={(e) => onChange({ date, time: e.target.value, timezone })}
          />
        </div>
      </div>

      <div className="schedule__field">
        <span className="selector__label" id="schedule-tz-label">
          Timezone
        </span>
        <Select
          value={timezone}
          options={zoneOptions}
          onChange={(tz) => onChange({ date, time, timezone: tz })}
          disabled={disabled}
          aria-labelledby="schedule-tz-label"
        />
      </div>

      {preview && (
        <p className="schedule__preview">
          <IconClock />
          <span>{preview}</span>
        </p>
      )}

      {error && (
        <p className="field__error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

function offsetLabel(tz: string): string {
  const now = DateTime.now().setZone(tz);
  return now.isValid ? now.toFormat("ZZZZ") : "";
}

/** Human confirmation of exactly when this will publish. */
export function describe(date: string, time: string, timezone: string): string | null {
  if (!date || !time) return null;
  const dt = DateTime.fromISO(`${date}T${time}`, { zone: timezone });
  if (!dt.isValid) return null;
  return `${dt.toFormat("cccc, d LLLL yyyy 'at' h:mm a")} (${dt.toFormat("ZZZZ")})`;
}

export function buildScheduledAt(date: string, time: string, timezone: string): string | null {
  if (!date || !time) return null;
  const dt = DateTime.fromISO(`${date}T${time}`, { zone: timezone });
  if (!dt.isValid) return null;
  // Naive local datetime. The publishing engine performs the UTC conversion.
  return dt.toFormat("yyyy-MM-dd'T'HH:mm:ss");
}

export function isFuture(date: string, time: string, timezone: string): boolean {
  const dt = DateTime.fromISO(`${date}T${time}`, { zone: timezone });
  if (!dt.isValid) return false;
  return dt > DateTime.now().plus({ minutes: 1 });
}

/**
 * A sensible default schedule: the next half hour, or the next five minutes if
 * we are already past the half hour.
 *
 * Luxon DateTimes are immutable - `plus()` and `startOf()` both return new
 * instances - so the result has to be reassigned. The previous version called
 * them without keeping the result, which made the rounding a no-op.
 */
export function nextSensibleSlot(timezone: string): { date: string; time: string } {
  const now = DateTime.now().setZone(timezone);
  let candidate = now.plus({ hours: 2 });
  candidate =
    candidate.minute < 30
      ? candidate.startOf("hour").plus({ minutes: 30 })
      : candidate.startOf("minute").plus({ minutes: 5 });
  return {
    date: candidate.toFormat("yyyy-MM-dd"),
    time: candidate.toFormat("HH:mm"),
  };
}

function IconClock() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.9"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </svg>
  );
}