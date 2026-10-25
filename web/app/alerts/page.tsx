"use client";

import Link from "next/link";
import { Attribution, DataNotices, Head, Problem, Skeleton } from "@/components/chrome";
import * as Icon from "@/components/icons";
import type { AlertTrip, Alerts } from "@/lib/api";
import { hhmm, lateness } from "@/lib/format";
import { usePoll } from "@/lib/usePoll";

const REASON: Record<AlertTrip["reason"], string> = {
  cancelled: "Cancelled",
  diverted: "Diverted",
  at_risk: "May not run",
  very_late: "Very late",
};

export default function AlertsPage() {
  const alerts = usePoll<Alerts>("/v1/alerts", 30_000);
  const routes = alerts.data?.routes ?? [];

  return (
    <main className="page">
      <Head title="Alerts" sub="Cancelled, diverted, likely no-shows and very late buses, from our own live data." back={false} />
      <DataNotices fetchedAt={alerts.fetchedAt} offline={alerts.offline} />

      <section className="section">
        {alerts.data ? (
          routes.length === 0 ? (
            <div className="empty">
              <p>
                <b>Nothing to report right now.</b>
              </p>
              <p className="muted">No cancellations, diversions or buses at high risk of not showing. This page updates every 30 seconds.</p>
            </div>
          ) : (
            <ul className="list">
              {routes.map((r) => (
                <li key={r.route} style={{ padding: "12px 14px" }}>
                  <div className="arr-top">
                    <Link className="route" href={`/route/${encodeURIComponent(r.route)}`} style={{ textDecoration: "none" }}>
                      {r.route}
                    </Link>
                    <p className="arr-dest">{summary(r)}</p>
                  </div>
                  <ul style={{ listStyle: "none", padding: 0, margin: "8px 0 0" }}>
                    {r.trips.map((t) => (
                      <li key={t.trip_id}>
                        <Link className="row-link" style={{ padding: "6px 0", minHeight: 44 }} href={`/trip/${encodeURIComponent(t.trip_id)}`}>
                          <span className={`badge ${t.reason === "at_risk" || t.reason === "very_late" ? "is-warn" : "is-stop"}`}>
                            {t.reason === "diverted" ? <Icon.Detour width={16} height={16} /> : <Icon.Warn width={16} height={16} />}
                            {REASON[t.reason]}
                          </span>
                          <span className="row-main small">
                            {hhmm(t.start_at)} to {t.headsign}
                            {t.reason === "very_late" && t.delay_s != null && `, ${lateness(t.delay_s)}`}
                            {t.reason === "at_risk" && t.noshow_prob != null && `, ${Math.round(t.noshow_prob * 100)}% chance it won't come`}
                          </span>
                          <Icon.Chevron className="chev" />
                        </Link>
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          )
        ) : alerts.error ? (
          <Problem error={alerts.error} retry={alerts.refresh} />
        ) : (
          <Skeleton />
        )}
      </section>
      <p className="muted small section">
        Times are scheduled start times. Official service notices are on the operators&apos; own sites; this page only reports what the live feed
        and bus GPS show.
      </p>
      <Attribution text={alerts.data?.attribution} />
    </main>
  );
}

function summary(r: Alerts["routes"][number]): string {
  const bits = [
    r.cancelled && `${r.cancelled} cancelled`,
    r.diverted && `${r.diverted} diverted`,
    r.at_risk && `${r.at_risk} may not run`,
    r.very_late && `${r.very_late} very late`,
  ].filter(Boolean);
  return bits.join(", ");
}
