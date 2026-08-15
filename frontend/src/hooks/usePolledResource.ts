import { useEffect, useRef, useState, useCallback } from "react";

interface PolledResourceOptions {
  intervalMs: number;
  enabled?: boolean;
}

interface PolledResourceState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  lastUpdated: number | null;
  refetch: () => void;
}

// Mirrors the adaptive poller in app/battles/page.tsx: single-flight guard,
// document.hidden gating, visibilitychange refetch, recursive setTimeout.
export function usePolledResource<T>(
  fetcher: () => Promise<T>,
  opts: PolledResourceOptions
): PolledResourceState<T> {
  const { intervalMs, enabled = true } = opts;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);

  // The caller passes an inline arrow that changes identity every render.
  // Reading it via a ref (kept fresh, never itself a dependency) means the
  // effect below only restarts on interval/enabled changes, not every render.
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const [refetchTick, setRefetchTick] = useState(0);
  const refetch = useCallback(() => setRefetchTick((n) => n + 1), []);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }

    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let hidden = typeof document !== "undefined" ? document.hidden : false;
    let inFlight = false;

    const load = async () => {
      if (!alive || hidden || inFlight) return;
      inFlight = true;
      try {
        const result = await fetcherRef.current();
        if (!alive) return;
        setData(result);
        setLastUpdated(Date.now());
        setError(null);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e : new Error("Failed to fetch"));
      } finally {
        inFlight = false;
        if (alive) setLoading(false);
      }
      if (alive && !hidden) timer = setTimeout(() => load(), intervalMs);
    };

    const onVisibility = () => {
      hidden = document.hidden;
      if (!hidden && alive) {
        if (timer) clearTimeout(timer);
        load();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    load();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [intervalMs, enabled, refetchTick]);

  return { data, error, loading, lastUpdated, refetch };
}
