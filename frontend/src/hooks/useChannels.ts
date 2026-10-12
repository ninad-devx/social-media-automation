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
 * Signal that the channel list changed on the server.
 *
 * `useChannels` is mounted independently in four places (Dashboard, Quick
 * Publish, Accounts, the selectors), and each one only refetched when the
 * selected *account* changed. Importing a channel in Settings therefore left
 * every other screen showing the old list until a manual reload, which read as
 * "the link did not work" even though the backend had it all along.
 *
 * The mutation that knows about the change calls `notifyChannelsChanged()` and
 * every mounted consumer refetches. Deliberately a plain subscription: the app
 * has no cache library, and a channel list is small enough that refetching is
 * cheaper than maintaining one.
 */
const listeners = new Set<() => void>();

export function notifyChannelsChanged(): void {
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Loads channels for the selected account.
 *
 * This is the whole "account -> channel" relationship. There is no per-account
 * branching anywhere in the UI: switch the account and this refetches.
 */
export function useChannels(accountId: string | null) {
  const [state, setState] = useState<ChannelsState>(EMPTY);
  const requestRef = useRef(0);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => subscribe(() => setReloadToken((n) => n + 1)), []);

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
  }, [accountId, reloadToken]);

  return state;
}