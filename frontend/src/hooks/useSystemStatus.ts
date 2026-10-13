/**
 * A shared, cancellable hook for the system status endpoint.
 */
import { useCallback, useEffect, useState } from "react";
import { systemApi } from "../services/integrations";
import type { SystemStatus } from "../types/integration";

/**
 * Read-only system status.
 *
 * Extracted so the Dashboard, the Settings overview and the Sidebar can all
 * agree on what is actually connected. The audit found the Dashboard printing
 * a hardcoded green "Online" and the Sidebar printing "n8n -> Buffer" on a
 * deployment running the direct engine.
 *
 * `probe` is opt-in because it makes a real network call to n8n.
 */
export function useSystemStatus(probe = false) {
  const [data, setData] = useState<SystemStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setError(null);
      try {
        setData(await systemApi.status(probe, signal));
      } catch (err) {
        // Previously the error was swallowed and the page kept rendering the
        // previous green status, so a failed refresh looked like success.
        setError(err instanceof Error ? err.message : "Could not read system status.");
      } finally {
        setLoading(false);
      }
    },
    [probe],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  return { data, error, loading, reload: () => load() };
}