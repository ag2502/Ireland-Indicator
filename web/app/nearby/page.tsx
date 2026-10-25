"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Attribution, DataNotices, Head, Problem, Skeleton } from "@/components/chrome";
import * as Icon from "@/components/icons";
import type { Stop } from "@/lib/api";
import { haversine } from "@/lib/device";
import { distance } from "@/lib/format";
import { usePoll } from "@/lib/usePoll";

type Fix = { lat: number; lon: number; accuracy: number };
const WALK_M_PER_MIN = 80;

export default function Nearby() {
  // the whole stop list comes down once a day and the distance maths runs here:
  // your location is never sent anywhere
  const stops = usePoll<Stop[]>("/v1/stops", 24 * 3600_000);
  const [fix, setFix] = useState<Fix | null>(null);
  const [geoError, setGeoError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);

  const locate = () => {
    if (!("geolocation" in navigator)) {
      setGeoError("This browser cannot share your location. Search for your stop on the home screen instead.");
      return;
    }
    setAsking(true);
    setGeoError(null);
    navigator.geolocation.getCurrentPosition(
      (p) => {
        setFix({ lat: p.coords.latitude, lon: p.coords.longitude, accuracy: p.coords.accuracy });
        setAsking(false);
      },
      (e) => {
        setAsking(false);
        setGeoError(
          e.code === e.PERMISSION_DENIED
            ? "Location is off for this site. Turn it on in your browser settings, or search for your stop on the home screen."
            : "Could not get a location fix. Step outside or try again.",
        );
      },
      { enableHighAccuracy: true, timeout: 15_000, maximumAge: 60_000 },
    );
  };

  const near = useMemo(() => {
    if (!fix || !stops.data) return null;
    return stops.data
      .map((s) => ({ s, d: haversine(fix.lat, fix.lon, s.stop_lat, s.stop_lon) }))
      .sort((a, b) => a.d - b.d)
      .slice(0, 12);
  }, [fix, stops.data]);

  return (
    <main className="page">
      <Head title="Stops near you" sub="Worked out on your phone. Your location is not sent to us." back={false} />
      <DataNotices />

      <section className="section">
        {!fix && (
          <div className="empty">
            <p>
              <b>Find the stops closest to you.</b>
            </p>
            <p className="muted">Your phone asks for permission once. We only use your location on this screen.</p>
            <button className="btn is-primary" onClick={locate} disabled={asking} style={{ marginTop: 12 }}>
              <Icon.Pin />
              {asking ? "Finding you…" : "Use my location"}
            </button>
            {geoError && (
              <p className="notice is-stop" role="alert">
                {geoError}
              </p>
            )}
          </div>
        )}

        {fix && stops.error && !stops.data && <Problem error={stops.error} retry={stops.refresh} />}
        {fix && !stops.data && !stops.error && <Skeleton rows={4} />}

        {near && (
          <>
            <p className="muted small" style={{ marginBottom: 8 }}>
              Walking times assume {WALK_M_PER_MIN} m a minute in a straight line, so allow a bit more.
              {fix && fix.accuracy > 150 && ` Your location is only accurate to about ${distance(fix.accuracy)}.`}
            </p>
            <ul className="list">
              {near.map(({ s, d }) => (
                <li key={s.stop_id}>
                  <Link className="row-link" href={`/stop/${encodeURIComponent(s.stop_id)}`}>
                    <span className="row-main">
                      <span className="row-title">{s.stop_name}</span>
                      <span className="muted small" style={{ display: "block" }}>
                        {s.stop_code ? `Stop ${s.stop_code}, ` : ""}
                        {distance(d)}, about {Math.max(1, Math.round(d / WALK_M_PER_MIN))} min walk
                      </span>
                    </span>
                    <Icon.Chevron className="chev" />
                  </Link>
                </li>
              ))}
            </ul>
            <button className="btn" onClick={locate} style={{ marginTop: 12 }}>
              <Icon.Pin />
              Update my location
            </button>
          </>
        )}
      </section>
      <Attribution />
    </main>
  );
}
