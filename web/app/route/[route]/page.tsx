"use client";

import Link from "next/link";
import { use, useState } from "react";
import { Attribution, DataNotices, Head, Problem, Skeleton } from "@/components/chrome";
import * as Icon from "@/components/icons";
import type { RouteReliability, Score, Vehicle } from "@/lib/api";
import { lateness, pct, secs } from "@/lib/format";
import { usePoll } from "@/lib/usePoll";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export default function RoutePage({ params }: { params: Promise<{ route: string }> }) {
  const { route } = use(params);
  const name = decodeURIComponent(route);
  const [days, setDays] = useState(7);
  const rel = usePoll<RouteReliability>(`/v1/routes/${encodeURIComponent(name)}/reliability?days=${days}`, 600_000);
  const live = usePoll<Vehicle[]>(`/v1/vehicles?route=${encodeURIComponent(name)}`, 30_000, false);
  const r = rel.data;

  return (
    <main className="page">
      <Head
        title={
          <span style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span className="route" style={{ fontSize: 26 }}>
              {r?.route ?? name}
            </span>
            <span style={{ fontSize: 22 }}>{r?.long_name ?? "Route"}</span>
          </span>
        }
        sub={`Scored on the last ${days} days`}
      />
      <DataNotices fetchedAt={rel.fetchedAt} offline={rel.offline} />

      <div className="section" role="group" aria-label="Period" style={{ display: "flex", gap: 8 }}>
        {[7, 30].map((d) => (
          <button key={d} className={`btn${d === days ? " is-primary" : ""}`} aria-pressed={d === days} onClick={() => setDays(d)}>
            Last {d} days
          </button>
        ))}
      </div>

      {r ? (
        <>
          <section className="section" aria-label="Summary">
            <Figures s={r.summary} />
          </section>

          <section className="section" aria-labelledby="live-h">
            <h2 className="section-title" id="live-h">
              On the road now
            </h2>
            {live.data && live.data.length > 0 ? (
              <ul className="list">
                {live.data.map((b) => (
                  <li key={b.trip_id ?? b.vehicle_id}>
                    <Link className="row-link" href={`/trip/${encodeURIComponent(b.trip_id ?? "")}`}>
                      {b.off_route || b.status === "diverted" ? <Icon.Detour width={22} height={22} /> : <Icon.Gps width={22} height={22} />}
                      <span className="row-main">
                        <span className="row-title">To {b.headsign}</span>
                        <span className="muted small" style={{ display: "block" }}>
                          {lateness(b.delay_s) || "Position only"}
                          {b.off_route ? ", off its usual route" : ""}
                        </span>
                      </span>
                      <Icon.Chevron className="chev" />
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">{live.data ? "No bus on this route is sending GPS right now." : "Loading…"}</p>
            )}
          </section>

          {r.daily.length > 1 && (
            <section className="section" aria-labelledby="daily-h">
              <h2 className="section-title" id="daily-h">
                Day by day
              </h2>
              <div className="panel">
                <div className="bars" role="img" aria-label={r.daily.map((d) => `${d.service_date}: ${pct(d.reliability_pct)}`).join("; ")}>
                  {r.daily.map((d) => (
                    <div key={d.service_date} title={`${d.service_date}: ${pct(d.reliability_pct)}`} style={{ height: `${d.reliability_pct ?? 0}%` }} />
                  ))}
                </div>
                <p className="muted small" style={{ marginTop: 6 }}>
                  Reliability each day, oldest on the left.
                </p>
              </div>
            </section>
          )}

          {r.heatmap.length > 0 && (
            <section className="section" aria-labelledby="heat-h">
              <h2 className="section-title" id="heat-h">
                When it keeps time
              </h2>
              <Heatmap cells={r.heatmap} />
            </section>
          )}

          {r.slowest_segments.length > 0 && (
            <section className="section" aria-labelledby="seg-h">
              <h2 className="section-title" id="seg-h">
                Where it loses time
              </h2>
              <ol className="list">
                {r.slowest_segments.map((s) => (
                  <li key={`${s.direction_id}-${s.from_stop_id}`} style={{ padding: "10px 14px" }}>
                    <p className="row-title">
                      {s.from_name ?? s.from_stop_id} to {s.to_name ?? s.to_stop_id}
                    </p>
                    <p className="muted small">
                      Typically {secs(s.median_lost_s)} slower than timetabled ({s.n} trips)
                    </p>
                  </li>
                ))}
              </ol>
            </section>
          )}
        </>
      ) : rel.error ? (
        <div className="section">
          <Problem error={rel.error} retry={rel.refresh} />
        </div>
      ) : (
        <div className="section">
          <Skeleton rows={3} />
        </div>
      )}
      <Attribution text={r?.attribution} />
    </main>
  );
}

function Figures({ s }: { s: Score }) {
  if (!s.n_trips) {
    return (
      <div className="empty">
        <p>
          <b>No scored trips in this period yet.</b> Scores are worked out each morning from the day before.
        </p>
      </div>
    );
  }
  return (
    <div className="figures">
      <div className="figure">
        <p className="n">{pct(s.reliability_pct)}</p>
        <p>of {s.n_trips} scheduled trips ran and kept time</p>
      </div>
      <div className="figure">
        <p className="n">{pct(s.noshow_pct, 1)}</p>
        <p>never showed up</p>
      </div>
      <div className="figure">
        <p className="n">{pct(s.on_time_pct)}</p>
        <p>of departures between 1 min early and 5 min late</p>
      </div>
      <div className="figure">
        <p className="n">{s.ewt_s != null ? secs(s.ewt_s) : pct(s.bunched_pct)}</p>
        <p>{s.ewt_s != null ? "extra wait beyond the timetable on average" : "of buses bunched behind the one in front"}</p>
      </div>
    </div>
  );
}

function Heatmap({ cells }: { cells: RouteReliability["heatmap"] }) {
  const at = new Map(cells.map((c) => [`${c.weekday}-${c.hour}`, c]));
  return (
    <div className="panel">
      <div className="heat" role="img" aria-label="Share of on-time departures by weekday and hour; darker green is better">
        <span className="lab" />
        {Array.from({ length: 24 }, (_, h) => (
          <span key={h} className="lab" style={{ textAlign: "center" }}>
            {h % 6 === 0 ? h : ""}
          </span>
        ))}
        {DAYS.map((d, w) => (
          <Row key={d} label={d} w={w} at={at} />
        ))}
      </div>
      <p className="muted small" style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ width: 14, height: 14, background: "var(--track)", borderRadius: 2 }} /> no service
        <span style={{ width: 14, height: 14, background: "color-mix(in srgb, var(--sign) 25%, var(--paper))", borderRadius: 2 }} /> often off time
        <span style={{ width: 14, height: 14, background: "var(--sign)", borderRadius: 2 }} /> keeps time
      </p>
    </div>
  );
}

function Row({ label, w, at }: { label: string; w: number; at: Map<string, RouteReliability["heatmap"][number]> }) {
  return (
    <>
      <span className="lab">{label}</span>
      {Array.from({ length: 24 }, (_, h) => {
        const c = at.get(`${w}-${h}`);
        const share = c?.on_time_share;
        const bg = share == null ? "var(--track)" : `color-mix(in srgb, var(--sign) ${Math.round(15 + share * 85)}%, var(--paper))`;
        return <span key={h} style={{ background: bg }} title={share == null ? `${label} ${h}:00, no data` : `${label} ${h}:00, ${pct(share * 100)} on time`} />;
      })}
    </>
  );
}
