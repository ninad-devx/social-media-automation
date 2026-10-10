import { useEffect, useRef, useState } from "react";
import { accountsApi } from "../services/accounts";
import type { Channel } from "../types/channel";
import { ApiError, NetworkError } from "../services/api";

interface ChannelsState {
  channels: Channel[];
  loading: boolean;
  error: string | null;
}

const EMPTY: ChannelsState = { channels: [], loading: false, error: null };

/**
 * Loads channels for the selected account.
 *
 * This is the whole "account -> channel" relationship. There is no per-account
 * branching anywhere in the UI: switch the account and this refetches.
 */
export function useChannels(accountId: string | null) {
  const [state, setState] = useState<ChannelsState>(EMPTY);
  const requestRef = useRef(0);

  useEffect(() => {
    if (!accountId) {
      setState(EMPTY);
      return;
    }

    const controller = new AbortController();
    const ticket = ++requestRef.current;

    setState({ channels: [], loading: true, error: null });

    accountsApi
      .channels(accountId, controller.signal)
      .then((channels) => {
        // Ignore responses from a superseded request.
        if (ticket !== requestRef.current) return;
        setState({ channels, loading: false, error: null });
      })
      .catch((err: unknown) => {
        if ((err as Error)?.name === "AbortError" || ticket !== requestRef.current) return;
        const message =
          err instanceof NetworkError || err instanceof ApiError
            ? err.message
            : "We couldn't load channels for this account.";
        setState({ channels: [], loading: false, error: message });
      });

    return () => controller.abort();
  }, [accountId]);

  return state;
}