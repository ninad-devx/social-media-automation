import { useCallback, useEffect, useState } from "react";
import { accountsApi } from "../services/accounts";
import type { Account } from "../types/account";
import { ApiError, NetworkError } from "../services/api";

interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

/**
 * Loads active accounts. Accounts are data, so adding one in the backend
 * makes it appear here with no frontend change.
 */
export function useAccounts() {
  const [state, setState] = useState<AsyncState<Account[]>>({
    data: null,
    loading: true,
    error: null,
  });

  const load = useCallback(async (signal?: AbortSignal) => {
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const accounts = await accountsApi.list(signal);
      setState({ data: accounts, loading: false, error: null });
    } catch (err) {
      if ((err as Error)?.name === "AbortError") return;
      const message =
        err instanceof NetworkError
          ? err.message
          : err instanceof ApiError
            ? err.message
            : "We couldn't load your accounts.";
      setState({ data: null, loading: false, error: message });
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  return { ...state, reload: () => load() };
}