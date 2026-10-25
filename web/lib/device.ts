"use client";

// Everything personal stays on the phone: favourite stops, walk times, location.

import { useCallback, useSyncExternalStore } from "react";

export type Fav = { stop_id: string; name: string; code: string | null };

const FAVS = "bt:favs";
const WALK = "bt:walk:";
const listeners = new Set<() => void>();

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {}
  listeners.forEach((l) => l());
}

function subscribe(l: () => void) {
  listeners.add(l);
  const onStorage = () => l();
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(l);
    window.removeEventListener("storage", onStorage);
  };
}

let favCache: { raw: string | null; value: Fav[] } = { raw: null, value: [] };
function favSnapshot(): Fav[] {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(FAVS);
  } catch {}
  if (raw !== favCache.raw) favCache = { raw, value: read<Fav[]>(FAVS, []) };
  return favCache.value;
}
const NO_FAVS: Fav[] = [];

export function useFavs() {
  const favs = useSyncExternalStore(subscribe, favSnapshot, () => NO_FAVS);
  const toggle = useCallback((f: Fav) => {
    const cur = read<Fav[]>(FAVS, []);
    write(FAVS, cur.some((x) => x.stop_id === f.stop_id) ? cur.filter((x) => x.stop_id !== f.stop_id) : [...cur, f]);
  }, []);
  return { favs, toggle, has: (id: string) => favs.some((f) => f.stop_id === id) };
}

export function useWalk(stopId: string): [number | null, (m: number | null) => void] {
  const walk = useSyncExternalStore(
    subscribe,
    () => read<number | null>(WALK + stopId, null),
    () => null,
  );
  const set = useCallback((m: number | null) => write(WALK + stopId, m), [stopId]);
  return [walk, set];
}

/** Great-circle distance in metres. */
export function haversine(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const r = 6371000;
  const p1 = (lat1 * Math.PI) / 180;
  const p2 = (lat2 * Math.PI) / 180;
  const dp = p2 - p1;
  const dl = ((lon2 - lon1) * Math.PI) / 180;
  const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * r * Math.asin(Math.sqrt(a));
}
