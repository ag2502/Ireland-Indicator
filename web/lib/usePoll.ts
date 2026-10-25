"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getJson } from "./api";

type State<T> = {
  data: T | null;
  error: ApiError | Error | null;
  /** epoch seconds the data was fetched; older than now when it came from the offline copy */
  fetchedAt: number | null;
  offline: boolean;
  loading: boolean;
};

const KEY = "bt:cache:";

function readCache<T>(path: string): { data: T; at: number } | null {
  try {
    const raw = localStorage.getItem(KEY + path);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writeCache(path: string, data: unknown) {
  try {
    localStorage.setItem(KEY + path, JSON.stringify({ data, at: Math.floor(Date.now() / 1000) }));
  } catch {}
}

/**
 * Fetch `path` now and every `everyMs` while the tab is visible. The last good answer is
 * kept on the device, so with no signal the screen shows it with its age rather than
 * going blank. `path` null skips fetching (e.g. waiting for location).
 */
export function usePoll<T>(path: string | null, everyMs = 30_000, keep = true): State<T> & { refresh: () => void } {
  const [state, setState] = useState<State<T>>({
    data: null,
    error: null,
    fetchedAt: null,
    offline: false,
    loading: path !== null,
  });
  const ctrl = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    if (!path) return;
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    try {
      const data = await getJson<T>(path, c.signal);
      if (keep) writeCache(path, data);
      setState({ data, error: null, fetchedAt: Math.floor(Date.now() / 1000), offline: false, loading: false });
    } catch (e) {
      if (c.signal.aborted) return;
      const err = e as Error;
      const isNetwork = !(err instanceof ApiError);
      const cached = isNetwork && keep ? readCache<T>(path) : null;
      setState((s) => ({
        data: cached?.data ?? (isNetwork ? s.data : null),
        error: err,
        fetchedAt: cached?.at ?? s.fetchedAt,
        offline: isNetwork,
        loading: false,
      }));
    }
  }, [path, keep]);

  useEffect(() => {
    if (!path) return;
    const cached = keep ? readCache<T>(path) : null;
    // show the device copy while the network answers
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setState({ data: cached?.data ?? null, error: null, fetchedAt: cached?.at ?? null, offline: false, loading: true });
    load();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, everyMs);
    const onVisible = () => document.visibilityState === "visible" && load();
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("online", load);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("online", load);
      ctrl.current?.abort();
    };
  }, [path, everyMs, keep, load]);

  return { ...state, refresh: load };
}

/** Current epoch seconds, ticking every `everyMs` so countdowns stay honest between polls. */
export function useNow(everyMs = 15_000): number {
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000));
  useEffect(() => {
    const id = window.setInterval(() => setNow(Math.floor(Date.now() / 1000)), everyMs);
    return () => window.clearInterval(id);
  }, [everyMs]);
  return now;
}
