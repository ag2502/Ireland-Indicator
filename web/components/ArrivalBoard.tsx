"use client";

import Link from "next/link";
import type { Arrival } from "@/lib/api";
import { countdown, hhmm, minsUntil, noShowLine, statusLine } from "@/lib/format";
import * as Icon from "./icons";

const HORIZON_MIN = 60;
const TICKS = [0, 5, 10, 20, 30, 60];

/** Square-root minute scale: the next 15 minutes get half the strip, where decisions are made. */
function x(epoch: number, now: number): number {
  const m = Math.min(Math.max((epoch - now) / 60, 0), HORIZON_MIN);
  return Math.sqrt(m / HORIZON_MIN) * 100;
}

export function ArrivalBoard({ arrivals, now, walkMin }: { arrivals: Arrival[]; now: number; walkMin: number | null }) {
  const gone = arrivals.filter((a) => a.status === "departed");
  const live = arrivals.filter((a) => a.status !== "departed");
  const hasModel = live.some((a) => a.early_at != null);

  return (
    <div className="arrivals">
      {live.length > 0 && (
        <div className="scale" aria-hidden="true">
          <div className="legend">
            <span>
              <i className="key-official" />
              Official time
            </span>
            {hasModel && (
              <>
                <span>
                  <i className="key-likely" />
                  Likely
                </span>
                <span>
                  <i className="key-range" />8 in 10 buses arrive in this range
                </span>
              </>
            )}
          </div>
          <div className="scale-axis">
            {TICKS.map((m) => (
              <span key={m} style={{ left: `${Math.sqrt(m / HORIZON_MIN) * 100}%`, ...(m === 60 ? { transform: "translateX(-100%)" } : {}) }}>
                {m === 0 ? "Now" : m === 60 ? "60 min" : m}
              </span>
            ))}
          </div>
        </div>
      )}

      {live.length === 0 ? (
        <div className="empty">
          <p>
            <b>No buses due in the next hour.</b>
          </p>
          <p className="muted">Late at night or on a quiet route this is normal. Check the timetable for the first bus tomorrow.</p>
        </div>
      ) : (
        <>
          {!hasModel && (
            <p className="muted small" style={{ marginBottom: 8 }}>
              Our own predictions are not switched on yet, so these are the official times. Status, GPS and cancellations are live.
            </p>
          )}
          <ul className="list">
            {live.map((a) => (
              <Row key={`${a.trip_id}-${a.scheduled_at}`} a={a} now={now} walkMin={walkMin} />
            ))}
          </ul>
        </>
      )}

      {gone.length > 0 && (
        <details className="section">
          <summary className="btn" style={{ width: "100%", justifyContent: "flex-start" }}>
            <Icon.Gone />
            Left in the last 15 minutes ({gone.length})
          </summary>
          <ul className="list" style={{ marginTop: 8 }}>
            {gone.map((a) => (
              <Row key={`${a.trip_id}-${a.scheduled_at}`} a={a} now={now} walkMin={null} />
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function Row({ a, now, walkMin }: { a: Arrival; now: number; walkMin: number | null }) {
  const upcoming = a.likely_at != null;
  const notComing = a.status === "cancelled" || a.status === "skipped";
  const gone = a.status === "departed";
  const noShow = noShowLine(a.noshow_prob, a.noshow_risk);
  const range =
    a.early_at != null && a.late_at != null ? `${minsUntil(a.early_at, now)}–${minsUntil(a.late_at, now)} min` : null;

  return (
    <li className={`arr${gone ? " is-gone" : ""}`}>
      <div className="arr-top">
        <span className="route">{a.route ?? "?"}</span>
        <p className="arr-dest">
          <Link href={`/trip/${encodeURIComponent(a.trip_id)}`}>{a.headsign ?? "Unknown destination"}</Link>
        </p>
        {upcoming && a.likely_at != null && (
          <p className="arr-when">
            {countdown(a.likely_at, now)}
            <small>{a.early_at != null ? "likely" : a.official_source === "timetable" ? "timetable" : "official"}</small>
          </p>
        )}
        {notComing && (
          <p className="arr-when is-struck" aria-label={`${hhmm(a.scheduled_at)}, not coming`}>
            {hhmm(a.scheduled_at)}
          </p>
        )}
        {gone && <p className="arr-when" style={{ fontSize: 22 }}>Gone</p>}
      </div>

      {upcoming && (
        <div className="arr-facts">
          <span>
            {a.official_source === "timetable" ? "Timetable" : "Official"} <b>{hhmm(a.official_at)}</b>
            {a.official_at != null && ` (${countdown(a.official_at, now)})`}
          </span>
          {range && (
            <span>
              Range <b>{range}</b>
            </span>
          )}
        </div>
      )}

      {upcoming && a.likely_at != null && (
        <div className="strip" aria-hidden="true">
          {a.early_at != null && a.late_at != null && (
            <span className="strip-range" style={{ left: `${x(a.early_at, now)}%`, width: `${x(a.late_at, now) - x(a.early_at, now)}%` }} />
          )}
          {a.official_at != null && <span className="strip-official" style={{ left: `${x(a.official_at, now)}%` }} />}
          {a.early_at != null && <span className="strip-likely" style={{ left: `${x(a.likely_at, now)}%` }} />}
        </div>
      )}

      <p className="arr-status">
        <StatusIcon a={a} />
        {notComing ? (
          <span className="badge is-stop">{a.status === "cancelled" ? "Cancelled" : "Not stopping here"}</span>
        ) : null}
        <span>{notComing ? (a.status === "cancelled" ? "This bus is not coming." : "Diverted away from this stop.") : statusLine(a, now)}</span>
      </p>

      {noShow && upcoming && (
        <p className="arr-status">
          <span className={`badge ${a.noshow_risk === "high" ? "is-stop" : "is-warn"}`}>
            <Icon.Warn width={16} height={16} />
            {a.noshow_risk === "high" ? "High risk" : "Some risk"}
          </span>
          <span>{noShow}</span>
        </p>
      )}

      {walkMin != null && upcoming && a.leave_by_at != null && <LeaveBy a={a} now={now} walkMin={walkMin} />}
    </li>
  );
}

function StatusIcon({ a }: { a: Arrival }) {
  if (a.status === "cancelled") return <Icon.Cross style={{ color: "var(--stop)" }} />;
  if (a.status === "skipped" || a.position.off_route) return <Icon.Detour />;
  if (a.status === "departed") return <Icon.Gone />;
  if (a.status === "scheduled") return <Icon.Clock />;
  if (a.tracked) return <Icon.Gps />;
  return <Icon.Info />;
}

function LeaveBy({ a, now, walkMin }: { a: Arrival; now: number; walkMin: number }) {
  const lb = a.leave_by_at!;
  const lastChance = (a.likely_at ?? lb) - walkMin * 60;
  if (lb - now > 60)
    return (
      <p className="arr-status">
        <Icon.Clock />
        <span>
          Leave by <span className="leave">{hhmm(lb)}</span> (in {countdown(lb, now)})
        </span>
      </p>
    );
  if (lb - now >= -60)
    return (
      <p className="arr-status">
        <Icon.Clock />
        <span className="leave is-now">Leave now</span>
      </p>
    );
  if (lastChance >= now)
    return (
      <p className="arr-status">
        <Icon.Clock />
        <span>
          <span className="leave is-now">Leave now and hurry.</span> It could come early.
        </span>
      </p>
    );
  return (
    <p className="arr-status muted">
      <Icon.Clock />
      <span>At a {walkMin} min walk you would likely miss this one.</span>
    </p>
  );
}
