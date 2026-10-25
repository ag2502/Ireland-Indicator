"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Attribution, DataNotices, Head, Problem, Skeleton } from "@/components/chrome";
import * as Icon from "@/components/icons";
import type { Leaderboard, RouteRef } from "@/lib/api";
import { pct } from "@/lib/format";
import { usePoll } from "@/lib/usePoll";

export default function RoutesPage() {
  const [days, setDays] = useState(7);
  const board = usePoll<Leaderboard>(`/v1/leaderboard?days=${days}`, 600_000);
  const routes = usePoll<RouteRef[]>("/v1/routes", 3600_000);
  const [q, setQ] = useState("");
  const found = useMemo(() => {
    const t = q.trim().toUpperCase();
    if (!t) return [];
    return (routes.data ?? []).filter((r) => r.route_short_name?.toUpperCase().startsWith(t)).slice(0, 8);
  }, [q, routes.data]);

  const rows = board.data?.routes ?? [];
  return (
    <main className="page">
      <Head title="Which routes keep time" sub={`Every route scored on the last ${days} days, from GPS, in public.`} back={false} />
      <DataNotices fetchedAt={board.fetchedAt} offline={board.offline} />

      <div className="search">
        <label htmlFor="route-find" className="vh">
          Find a route
        </label>
        <Icon.Search />
        <input id="route-find" type="search" placeholder="Route number, e.g. 39A" value={q} onChange={(e) => setQ(e.target.value)} />
        {found.length > 0 && (
          <ul className="list" style={{ marginTop: 8 }}>
            {found.map((r) => (
              <li key={r.route_id}>
                <Link className="row-link" href={`/route/${encodeURIComponent(r.route_short_name ?? r.route_id)}`}>
                  <span className="route">{r.route_short_name}</span>
                  <span className="row-main small">{r.route_long_name}</span>
                  <Icon.Chevron className="chev" />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="section" role="group" aria-label="Period" style={{ display: "flex", gap: 8 }}>
        {[7, 30].map((d) => (
          <button key={d} className={`btn${d === days ? " is-primary" : ""}`} aria-pressed={d === days} onClick={() => setDays(d)}>
            Last {d} days
          </button>
        ))}
      </div>

      <section className="section" aria-labelledby="rank-h">
        <h2 className="section-title" id="rank-h">
          Most reliable first
        </h2>
        <p className="muted small" style={{ marginBottom: 8 }}>
          Reliability is the share of scheduled trips that ran and left each stop between 1 minute early and 5 minutes late. Routes with fewer than 20
          trips in the period are not ranked.
        </p>
        {board.data ? (
          rows.length === 0 ? (
            <div className="empty">
              <p>
                <b>Not enough days scored yet.</b> Rankings appear once routes have 20 or more trips on record.
              </p>
            </div>
          ) : (
            <ol className="list">
              {rows.map((r) => (
                <li key={r.route}>
                  <Link className="row-link" href={`/route/${encodeURIComponent(r.route)}`}>
                    <span className="rank" aria-label={`Rank ${r.rank}`}>
                      {r.rank}
                    </span>
                    <span className="route">{r.route}</span>
                    <span className="row-main">
                      <span className="row-title">{pct(r.reliability_pct)} reliable</span>
                      <span className="muted small" style={{ display: "block" }}>
                        {pct(r.on_time_pct)} on time, {pct(r.noshow_pct, 1)} never showed
                      </span>
                      <span className="meter" aria-hidden="true">
                        <div style={{ width: `${r.reliability_pct ?? 0}%` }} />
                      </span>
                    </span>
                    <Icon.Chevron className="chev" />
                  </Link>
                </li>
              ))}
            </ol>
          )
        ) : board.error ? (
          <Problem error={board.error} retry={board.refresh} />
        ) : (
          <Skeleton rows={5} />
        )}
      </section>
      <Attribution text={board.data?.attribution} />
    </main>
  );
}
