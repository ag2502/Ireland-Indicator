"use client";

import type { GeoJSONSource, Map as MlMap, MapLayerMouseEvent } from "maplibre-gl";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DataNotices } from "@/components/chrome";
import * as Icon from "@/components/icons";
import { MapView, token } from "@/components/MapView";
import type { Vehicle } from "@/lib/api";
import { ago, cap, lateness, tripStatusWord } from "@/lib/format";
import { useNow, usePoll } from "@/lib/usePoll";

const LATE_S = 300;

export default function LiveMap() {
  const [route, setRoute] = useState("");
  const q = route.trim();
  const buses = usePoll<Vehicle[]>(`/v1/vehicles${q ? `?route=${encodeURIComponent(q)}` : ""}`, 20_000, false);
  const now = useNow();
  const mapRef = useRef<MlMap | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const list = useMemo(() => buses.data ?? [], [buses.data]);
  const bus = list.find((b) => b.trip_id === picked) ?? null;

  const geojson = useMemo<GeoJSON.FeatureCollection>(
    () => ({
      type: "FeatureCollection",
      features: list.map((b) => ({
        type: "Feature",
        properties: {
          trip_id: b.trip_id,
          route: b.route ?? "",
          kind: b.status === "diverted" || b.off_route ? "diverted" : (b.delay_s ?? 0) >= LATE_S ? "late" : "ok",
        },
        geometry: { type: "Point", coordinates: [b.lon, b.lat] },
      })),
    }),
    [list],
  );
  const dataRef = useRef(geojson);

  const onReady = useCallback((map: MlMap) => {
    mapRef.current = map;
    const sign = token("--sign");
    map.addSource("buses", { type: "geojson", data: dataRef.current });
    map.addLayer({
      id: "buses",
      type: "circle",
      source: "buses",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 7, 15, 14],
        // shape and label carry the status too: late buses get a thick yellow ring, diverted a red one
        "circle-color": sign,
        "circle-stroke-width": ["match", ["get", "kind"], "ok", 2, 5],
        "circle-stroke-color": ["match", ["get", "kind"], "late", "#ffd400", "diverted", "#c8102e", "#ffffff"],
      },
    });
    map.addLayer({
      id: "bus-labels",
      type: "symbol",
      source: "buses",
      minzoom: 12,
      layout: {
        "text-field": ["get", "route"],
        "text-font": ["Noto Sans Bold"],
        "text-size": 12,
        "text-offset": [0, 1.6],
        "text-allow-overlap": false,
      },
      paint: { "text-color": "#000", "text-halo-color": "#fff", "text-halo-width": 2 },
    });
    map.on("click", "buses", (e: MapLayerMouseEvent) => setPicked(String(e.features?.[0]?.properties?.trip_id ?? "")));
    map.on("mouseenter", "buses", () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", "buses", () => (map.getCanvas().style.cursor = ""));
  }, []);

  useEffect(() => {
    dataRef.current = geojson;
    (mapRef.current?.getSource("buses") as GeoJSONSource | undefined)?.setData(geojson);
  }, [geojson]);

  const late = list.filter((b) => (b.delay_s ?? 0) >= LATE_S).length;
  const diverted = list.filter((b) => b.status === "diverted" || b.off_route).length;

  return (
    <main className="map-wrap">
      <MapView className="map" label="Live map of buses" zoom={11} onReady={onReady} />
      <div className="map-bar">
        <div className="plate" role="status" aria-live="polite">
          <b>{buses.data ? `${list.length} buses with GPS` : "Loading buses…"}</b>
          {buses.data && (
            <span className="small" style={{ display: "block" }}>
              {late} running 5+ min late, {diverted} diverted
            </span>
          )}
        </div>
        <label className="vh" htmlFor="route-q">
          Show one route
        </label>
        <input id="route-q" placeholder="Route" value={route} onChange={(e) => setRoute(e.target.value)} autoCapitalize="characters" />
      </div>
      <div style={{ position: "absolute", zIndex: 3, top: 86, left: 10, right: 10 }}>
        <DataNotices fetchedAt={buses.fetchedAt} offline={buses.offline} />
      </div>
      {bus && (
        <div className="map-sheet" role="dialog" aria-label={`Bus ${bus.route} to ${bus.headsign}`}>
          <div className="arr-top">
            <span className="route">{bus.route}</span>
            <p className="arr-dest">{bus.headsign}</p>
            <button className="head-btn" style={{ color: "var(--ink)" }} onClick={() => setPicked(null)} aria-label="Close">
              <Icon.Cross />
            </button>
          </div>
          <p className="arr-status">
            {bus.off_route || bus.status === "diverted" ? <Icon.Detour /> : <Icon.Gps />}
            <span>
              {tripStatusWord(bus.status)}
              {bus.delay_s != null ? `, ${lateness(bus.delay_s)}` : ""}
              {bus.off_route ? ", off its usual route" : ""}. GPS {ago(bus.ping_at, now)}.
            </span>
          </p>
          {bus.trip_id && (
            <Link className="btn is-primary" style={{ marginTop: 10, width: "100%" }} href={`/trip/${encodeURIComponent(bus.trip_id)}`}>
              {cap("see every stop on this trip")}
            </Link>
          )}
        </div>
      )}
    </main>
  );
}
