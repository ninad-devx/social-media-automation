import { useCallback, useRef, useState } from "react";
import { publishingApi, newIdempotencyKey } from "../services/publishing";
import type { PublishRequest, PublishResponse } from "../types/publishing";
import { ApiError, NetworkError } from "../services/api";

interface PublishState {
  publishing: boolean;
  result: PublishResponse | null;
  error: { code: string; message: string; field: string | null; requestId: string } | null;
}

const INITIAL: PublishState = { publishing: false, result: null, error: null };

/**
 * Owns the publish lifecycle, including double-submit protection.
 *
 * Two layers of protection:
 *  1. the button is disabled while `publishing` is true (UI)
 *  2. a stable Idempotency-Key is reused for retries of the same attempt (API)
 */
export function usePublish(onSuccess?: (result: PublishResponse) => void) {
  const [state, setState] = useState<PublishState>(INITIAL);
  const inFlight = useRef(false);
  const keyRef = useRef<string>(newIdempotencyKey());

  const reset = useCallback(() => {
    keyRef.current = newIdempotencyKey();
    setState(INITIAL);
  }, []);

  const publish = useCallback(
    async (payload: PublishRequest): Promise<PublishResponse | null> => {
      // Hard guard: ignore re-entrant calls even before React re-renders.
      if (inFlight.current) return null;
      inFlight.current = true;

      setState({ publishing: true, result: null, error: null });

      try {
        const result = await publishingApi.publish(payload, keyRef.current);
        setState({ publishing: false, result, error: null });
        // The callback runs outside the try on purpose. It used to sit inside,
        // so a throwing handler turned a successful publish into a reported
        // failure and cleared the result the caller had just received.
        try {
          onSuccess?.(result);
        } catch (err) {
          console.error("publish success handler failed", err);
        }
        return result;
      } catch (err) {
        const normalized =
          err instanceof ApiError
            ? { code: err.code, message: err.message, field: err.field, requestId: err.requestId }
            : err instanceof NetworkError
              ? { code: "NETWORK_ERROR", message: err.message, field: null, requestId: "" }
              : {
                  code: "INTERNAL_ERROR",
                  message: "Something went wrong. Please try again.",
                  field: null,
                  requestId: "",
                };

        setState({ publishing: false, result: null, error: normalized });
        return null;
      } finally {
        inFlight.current = false;
      }
    },
    [onSuccess],
  );

  return { ...state, publish, reset };
}