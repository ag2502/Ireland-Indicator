"use client";

import type { GeoJSONSource, Map as MlMap } from "maplibre-gl";
import Link from "next/link";
import { Fragment, use, useCallback, useEffect, useRef, useState } from "react";
import { Attribution, DataNotices, Head, Problem, Skeleton } from "@/components/chrome";
import * as Icon from "@/components/icons";
import { MapView, token } from "@/components/MapView";
import type { Trip } from "@/lib/api";
import { ago, countdown, hhmm, lateness, tripStatusWord } from "@/lib/format";
import { useNow, usePoll } from "@/lib/usePoll";

export default function TripPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const trip = usePoll<Trip>(`/v1/trips/${encodeURIComponent(decodeURIComponent(id))}`, 30_000);
  const now = useNow();
  const t = trip.data;

  return (
    <main className="page">
      <Head
        title={
          t ? (
            <span style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <span className="route" style={{ fontSize: 26 }}>
                {t.route}
              </span>
              <span>{t.headsign}</span>
            </span>
          ) : (
            "This bus"
          )
        }
        sub={t && <TripSummary t={t} now={now} />}
      />
      <DataNotices fetchedAt={trip.fetchedAt} offline={trip.offline} madeAt={t?.made_at} />

      {t?.status === "cancelled" && (
        <p className="notice is-stop" role="status">
          <Icon.Cross />
          <span>This trip is cancelled in the official feed. It will not run.</span>
        </p>
      )}
      {t && (t.status === "diverted" || t.off_route) && (
        <p className="notice" role="status">
          <Icon.Detour />
          <span>
            {t.off_route ? "Its GPS shows it off the usual route. " : ""}
            {t.stops.some((s) => s.state === "skipped") ? "It will not stop at the stops struck through below." : "Stops may be missed."}
          </span>
        </p>
      )}

      {t ? (
        <>
          <section className="section" aria-label="Map">
            <TripMap t={t} />
          </section>
          <section className="section" aria-labelledby="stops-h">
            <h2 className="section-title" id="stops-h">
              Stops
            </h2>
            <Journey t={t} now={now} />
          </section>
          {t.route && (
            <p className="section">
              <Link className="btn" href={`/route/${encodeURIComponent(t.route)}`}>
                <Icon.Signpost />
                How reliable is the {t.route}?
              </Link>
            </p>
          )}
        </>
      ) : trip.error ? (
        <div className="section">
          <Problem error={trip.error} retry={trip.refresh} />
          <p className="muted small" style={{ marginTop: 8 }}>
            We only follow buses that are on the road or due in the next hour.
          </p>
        </div>
      ) : (
        <div className="section">
          <Skeleton rows={4} />
        </div>
      )}
      <Attribution text={t?.attribution} />
    </main>
  );
}

function TripSummary({ t, now }: { t: Trip; now: number }) {
  const parts = [tripStatusWord(t.status)];
  if ((t.status === "running" || t.status === "diverted") && t.delay_s != null) parts.push(lateness(t.delay_s));
  if (t.status === "not_started" && t.start_at) parts.push(`due to start ${hhmm(t.start_at)}`);
  if (t.ping_at) parts.push(`GPS ${ago(t.ping_at, now)}`);
  else if (t.status === "running") parts.push("no GPS right now");
  return <>{parts.join(", ")}</>;
}

function Journey({ t, now }: { t: Trip; now: number }) {
  const lastPassed = t.stops.reduce((i, s, k) => (s.state === "passed" ? k : i), -1);
  const onRoad = t.status === "running" || t.status === "diverted";
  // where the bus is matters most: fold all but the last two stops behind it
  const [showAll, setShowAll] = useState(false);
  const hidden = showAll ? 0 : Math.max(0, lastPassed - 1);
  return (
    <>
      {hidden > 0 && (
        <button className="btn" style={{ width: "100%", marginBottom: 8 }} onClick={() => setShowAll(true)}>
          Show {hidden} earlier {hidden === 1 ? "stop" : "stops"} it has passed
        </button>
      )}
      <ol className="journey">
        {t.stops.map((s, k) =>
          k < hidden ? null : (
            <Fragment key={s.stop_sequence}>
              <li className={`is-${s.state}`}>
                <span className="dot" aria-hidden="true" />
                <Link className="stop-name" href={`/stop/${encodeURIComponent(s.stop_id)}`}>
                  {s.stop_name ?? s.stop_id}
                </Link>
                <span className="t small">
                  {s.state === "passed" && (s.passed_at ? `passed ${hhmm(s.passed_at)}` : "passed")}
                  {s.state === "skipped" && <span className="badge is-stop">Not stopping</span>}
                  {s.state === "cancelled" && <s>{hhmm(s.scheduled_at)}</s>}
                  {s.state === "upcoming" &&
                    (s.likely_at ? (
                      <>
                        <b>{countdown(s.likely_at, now)}</b> {hhmm(s.likely_at)}
                      </>
                    ) : s.scheduled_at <= now ? (
                      <>
                        <b>Due</b> <span className="muted">timetable {hhmm(s.scheduled_at)}</span>
                      </>
                    ) : (
                      <span className="muted">{hhmm(s.scheduled_at)} timetable</span>
                    ))}
                </span>
              </li>
              {onRoad && k === lastPassed && lastPassed < t.stops.length - 1 && (
                <li className="is-bus" aria-label="The bus is here">
                  <span className="dot" aria-hidden="true" />
                  <span className="stop-name">
                    <Icon.Bus width={18} height={18} style={{ verticalAlign: -3, marginRight: 6 }} />
                    Bus is here{t.delay_s != null ? `, ${lateness(t.delay_s)}` : ""}
                  </span>
                </li>
              )}
            </Fragment>
          ),
        )}
      </ol>
    </>
  );
}

function TripMap({ t }: { t: Trip }) {
  const mapRef = useRef<MlMap | null>(null);
  const tRef = useRef(t);

  const draw = useCallback((map: MlMap, trip: Trip) => {
    const stops: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: trip.stops
        .filter((s) => s.stop_lat != null && s.stop_lon != null)
        .map((s) => ({
          type: "Feature",
          properties: { state: s.state },
          geometry: { type: "Point", coordinates: [s.stop_lon!, s.stop_lat!] },
        })),
    };
    const bus: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features:
        trip.lat != null && trip.lon != null
          ? [{ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [trip.lon, trip.lat] } }]
          : [],
    };
    (map.getSource("stops") as GeoJSONSource | undefined)?.setData(stops);
    (map.getSource("bus") as GeoJSONSource | undefined)?.setData(bus);
  }, []);

  const onReady = useCallback(
    (map: MlMap) => {
      mapRef.current = map;
      const trip = tRef.current;
      const sign = token("--sign");
      const stop = token("--stop");
      map.addSource("line", {
        type: "geojson",
        data: { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: trip.shape } },
      });
      map.addSource("stops", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addSource("bus", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "line", type: "line", source: "line", paint: { "line-color": sign, "line-width": 5 } });
      map.addLayer({
        id: "stops",
        type: "circle",
        source: "stops",
        paint: {
          "circle-radius": 5,
          "circle-color": "#fff",
          "circle-stroke-width": 3,
          "circle-stroke-color": ["match", ["get", "state"], "skipped", stop, "cancelled", stop, sign],
        },
      });
      map.addLayer({
        id: "bus",
        type: "circle",
        source: "bus",
        paint: { "circle-radius": 10, "circle-color": "#000", "circle-stroke-width": 4, "circle-stroke-color": "#ffd400" },
      });
      draw(map, trip);
      const pts = trip.shape.length ? trip.shape : trip.stops.filter((s) => s.stop_lon != null).map((s) => [s.stop_lon!, s.stop_lat!]);
      if (pts.length) {
        const lons = pts.map((p) => p[0]);
        const lats = pts.map((p) => p[1]);
        map.fitBounds(
          [
            [Math.min(...lons), Math.min(...lats)],
            [Math.max(...lons), Math.max(...lats)],
          ],
          { padding: 30, duration: 0 },
        );
      }
    },
    [draw],
  );

  useEffect(() => {
    tRef.current = t;
    if (mapRef.current) draw(mapRef.current, t);
  }, [t, draw]);

  return <MapView className="mini-map" label={`Map of the ${t.route} to ${t.headsign}`} onReady={onReady} />;
}
