"use client";

import "maplibre-gl/dist/maplibre-gl.css";
import type { Map as MlMap } from "maplibre-gl";
import { useEffect, useRef, useState } from "react";

// OpenFreeMap: free OpenStreetMap vector tiles, no key. Style follows the phone's theme.
const STYLE = {
  light: "https://tiles.openfreemap.org/styles/positron",
  dark: "https://tiles.openfreemap.org/styles/dark",
};
export const DUBLIN: [number, number] = [-6.26, 53.35];

/**
 * A MapLibre map that calls `onReady` once its style has loaded. Data layers are the
 * caller's: they get the map and add sources to it.
 */
export function MapView({
  className,
  center = DUBLIN,
  zoom = 11,
  label,
  onReady,
}: {
  className?: string;
  center?: [number, number];
  zoom?: number;
  label: string;
  onReady: (map: MlMap) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const ready = useRef(onReady);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    ready.current = onReady;
  }, [onReady]);

  useEffect(() => {
    let map: MlMap | null = null;
    let cancelled = false;
    import("maplibre-gl")
      .then(({ default: ml }) => {
        if (cancelled || !box.current) return;
        const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
        map = new ml.Map({
          container: box.current,
          style: dark ? STYLE.dark : STYLE.light,
          center,
          zoom,
          attributionControl: { compact: true },
          dragRotate: false,
          pitchWithRotate: false,
        });
        map.touchZoomRotate.disableRotation();
        map.addControl(new ml.NavigationControl({ showCompass: false }), "bottom-right");
        map.on("load", () => map && ready.current(map));
        map.on("error", (e) => {
          if (!map?.isStyleLoaded() && String(e.error?.message ?? "").includes("style")) setFailed(true);
        });
      })
      .catch(() => setFailed(true));
    return () => {
      cancelled = true;
      map?.remove();
    };
    // the map is created once; later centre changes are the caller's to apply
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className={className} role="region" aria-label={label}>
      <div ref={box} style={{ position: "absolute", inset: 0 }} />
      {failed && (
        <p className="notice" style={{ position: "absolute", left: 10, right: 10, top: 10, margin: 0 }}>
          The map could not load. The list views still work.
        </p>
      )}
    </div>
  );
}

/** Read a CSS custom property, so map colours follow the same tokens as the page. */
export function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#00553f";
}
