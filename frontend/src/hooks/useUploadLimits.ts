import { useEffect, useState } from "react";
import {
  FALLBACK_UPLOAD_LIMITS,
  fetchUploadLimits,
  type UploadLimits,
} from "../services/media";

/**
 * The upload ceilings, read once from the server and shared.
 *
 * These used to be a hardcoded `25 * 1024 * 1024` in two places, which is why
 * a large Facebook Reel was refused by the browser before the backend ever saw
 * it - the backend had no opinion about it at all. One source of truth, owned
 * by the deployment that enforces it.
 */

let cached: UploadLimits | null = null;
let inflight: Promise<UploadLimits> | null = null;

function load(): Promise<UploadLimits> {
  if (cached) return Promise.resolve(cached);
  if (!inflight) {
    inflight = fetchUploadLimits()
      .then((limits) => {
        cached = limits;
        return limits;
      })
      .catch(() => FALLBACK_UPLOAD_LIMITS)
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

/** Exposed for tests and for a "retry limits" affordance. */
export function resetUploadLimitsCache() {
  cached = null;
  inflight = null;
}

export function useUploadLimits() {
  const [limits, setLimits] = useState<UploadLimits>(cached ?? FALLBACK_UPLOAD_LIMITS);

  useEffect(() => {
    if (cached) return;
    let active = true;
    void load().then((result) => {
      if (active) setLimits(result);
    });
    return () => {
      active = false;
    };
  }, []);

  return limits;
}
