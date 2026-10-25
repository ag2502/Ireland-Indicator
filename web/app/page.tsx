"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Attribution, DataNotices } from "@/components/chrome";
import * as Icon from "@/components/icons";
import { type Alerts, type Arrivals, type Stop, getJson } from "@/lib/api";
import { type Fav, useFavs } from "@/lib/device";
import { countdown } from "@/lib/format";
import { useNow, usePoll } from "@/lib/usePoll";

export default function Home() {
  const { favs } = useFavs();
  const alerts = usePoll<Alerts>("/v1/alerts", 60_000);
  const disrupted = alerts.data?.routes.length ?? null;

  return (
    <main className="page">
      <header className="plate head">
        <div className="head-main">
          <h1 style={{ fontSize: 34 }}>BusTrust</h1>
          <p className="sub">Unofficial. When your Dublin bus will really come, and if it will.</p>
        </div>
      </header>
      <DataNotices />

      <StopSearch />

      {favs.length > 0 && (
        <section className="section" aria-labelledby="favs">
          <h2 className="section-title" id="favs">
            Your stops
          </h2>
          <ul className="list">
            {favs.map((f) => (
              <FavRow key={f.stop_id} f={f} />
            ))}
          </ul>
        </section>
      )}

      <nav className="tiles" aria-label="Transport">
        <Link href="/nearby" className="plate tile tile-bus">
          <Icon.Bus />
          <span>
            <span className="plate-title">Bus</span>
            <br />
            <small>Stops near you, live arrivals</small>
          </span>
        </Link>
        <Link href="/alerts" className="plate tile tile-alerts">
          <Icon.Warn />
          <span>
            <span className="plate-title">Alerts</span>
            <br />
            <small>
              {disrupted == null ? "Live" : disrupted === 0 ? "No disruption now" : `${disrupted} ${disrupted === 1 ? "route" : "routes"} hit`}
            </small>
          </span>
        </Link>
        {[
          { name: "Luas", icon: Icon.Tram },
          { name: "DART", icon: Icon.Train },
          { name: "Bikes", icon: Icon.Bike },
          { name: "Fares", icon: Icon.Ticket },
        ].map(({ name, icon: Ic }) => (
          <div key={name} className="plate tile tile-soon" aria-disabled="true">
            <Ic />
            <span>
              <span className="plate-title">{name}</span>
              <br />
              <small>Coming soon</small>
            </span>
          </div>
        ))}
      </nav>

      <section className="section">
        <ul className="list">
          <li>
            <Link className="row-link" href="/map">
              <Icon.MapIcon width={22} height={22} />
              <span className="row-main">Live map of every bus</span>
              <Icon.Chevron className="chev" />
            </Link>
          </li>
          <li>
            <Link className="row-link" href="/routes">
              <Icon.Signpost width={22} height={22} />
              <span className="row-main">Which routes keep time: this week&apos;s scores</span>
              <Icon.Chevron className="chev" />
            </Link>
          </li>
          <li>
            <Link className="row-link" href="/how">
              <Icon.Info width={22} height={22} />
              <span className="row-main">How it works and how accurate we are</span>
              <Icon.Chevron className="chev" />
            </Link>
          </li>
        </ul>
      </section>

      <Attribution />
    </main>
  );
}

function StopSearch() {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Stop[] | null>(null);

  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) return;
    const c = new AbortController();
    const t = window.setTimeout(() => {
      getJson<Stop[]>(`/v1/stops?q=${encodeURIComponent(term)}&limit=8`, c.signal)
        .then(setHits)
        .catch(() => {});
    }, 200);
    return () => {
      window.clearTimeout(t);
      c.abort();
    };
  }, [q]);

  const shown = q.trim().length >= 2 ? hits : null;
  return (
    <div className="search" role="search">
      <label htmlFor="stop-q" className="vh">
        Find a stop by name or number
      </label>
      <Icon.Search />
      <input
        id="stop-q"
        type="search"
        inputMode="search"
        autoComplete="off"
        placeholder="Stop name or number"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      {shown && (
        <ul className="list" style={{ marginTop: 8 }} aria-live="polite">
          {shown.length === 0 ? (
            <li className="row-link muted">No stop matches “{q.trim()}”. Try the number on the stop pole.</li>
          ) : (
            shown.map((s) => (
              <li key={s.stop_id}>
                <Link className="row-link" href={`/stop/${encodeURIComponent(s.stop_id)}`}>
                  <span className="row-main">
                    <span className="row-title">{s.stop_name}</span>
                    {s.stop_code && <span className="muted small"> Stop {s.stop_code}</span>}
                  </span>
                  <Icon.Chevron className="chev" />
                </Link>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}

function FavRow({ f }: { f: Fav }) {
  const { data } = usePoll<Arrivals>(`/v1/stops/${encodeURIComponent(f.stop_id)}/arrivals`, 60_000);
  const now = useNow();
  const next = (data?.arrivals ?? []).filter((a) => a.likely_at != null).slice(0, 3);
  const cancelled = (data?.arrivals ?? []).filter((a) => a.status === "cancelled").length;
  return (
    <li>
      <Link className="row-link" href={`/stop/${encodeURIComponent(f.stop_id)}`}>
        <span className="row-main">
          <span className="row-title">{f.name}</span>
          <span className="small" style={{ display: "flex", flexWrap: "wrap", gap: "4px 12px", marginTop: 4 }}>
            {data == null ? (
              <span className="muted">Loading…</span>
            ) : next.length === 0 ? (
              <span className="muted">Nothing due in the next hour</span>
            ) : (
              next.map((a) => (
                <span key={a.trip_id}>
                  <b>{a.route}</b> {countdown(a.likely_at, now)}
                </span>
              ))
            )}
            {cancelled > 0 && <span className="badge is-stop">{cancelled} cancelled</span>}
          </span>
        </span>
        <Icon.Chevron className="chev" />
      </Link>
    </li>
  );
}
