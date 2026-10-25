import type { Arrival, TripStatus } from "./api";

const clock = new Intl.DateTimeFormat("en-IE", {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "Europe/Dublin",
});

/** 21:47, Dublin time. */
export function hhmm(epoch: number | null | undefined): string {
  return epoch == null ? "" : clock.format(new Date(epoch * 1000));
}

/** Whole minutes from now until `epoch`, never negative. */
export function minsUntil(epoch: number, now: number): number {
  return Math.max(0, Math.round((epoch - now) / 60));
}

/** "Due", "1 min", "12 min". Minutes only: the data is a minute old at best. */
export function countdown(epoch: number | null, now: number): string {
  if (epoch == null) return "";
  const m = minsUntil(epoch, now);
  return m < 1 ? "Due" : `${m} min`;
}

export function ago(epoch: number | null, now: number): string {
  if (epoch == null) return "";
  const m = Math.round((now - epoch) / 60);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  return `${h} h ${m % 60} min ago`;
}

/** "4 min late", "2 min early", "on time" from a delay in seconds. */
export function lateness(delay: number | null | undefined): string {
  if (delay == null) return "";
  const m = Math.round(Math.abs(delay) / 60);
  if (m < 1) return "on time";
  return delay > 0 ? `${m} min late` : `${m} min early`;
}

export function pct(x: number | null | undefined, digits = 0): string {
  return x == null ? "–" : `${x.toFixed(digits)}%`;
}

export function secs(x: number | null | undefined): string {
  if (x == null) return "–";
  if (Math.abs(x) < 90) return `${Math.round(x)} s`;
  return `${(x / 60).toFixed(1)} min`;
}

/** One plain sentence for what this bus is doing at this stop. */
export function statusLine(a: Arrival, now: number): string {
  switch (a.status) {
    case "cancelled":
      return "Cancelled. This bus is not coming.";
    case "skipped":
      return "Diverted. It will not stop here.";
    case "departed":
      return a.passed_at ? `Left ${ago(a.passed_at, now)}${a.delay_s != null ? `, ${lateness(a.delay_s)}` : ""}` : "Already gone";
    case "scheduled":
      return "Timetable time. The bus is not live yet.";
    default: {
      const parts = [cap(lateness(a.delay_s) || "on time")];
      if (a.position.stops_away != null && a.tracked) {
        const n = a.position.stops_away;
        parts.push(n === 0 ? "at the stop before" : n === 1 ? "1 stop away" : `${n} stops away`);
      }
      if (a.position.off_route) parts.push("off its usual route");
      return parts.join(", ");
    }
  }
}

export function tripStatusWord(s: TripStatus): string {
  return {
    not_started: "Not started",
    running: "On the road",
    diverted: "Diverted",
    cancelled: "Cancelled",
    finished: "Finished",
  }[s];
}

/** The candid no-show line, or null when the risk is not worth a rider's attention. */
export function noShowLine(p: number | null, risk: Arrival["noshow_risk"]): string | null {
  if (p == null || risk === "low" || risk === "cancelled" || risk == null) return null;
  const n = Math.round(p * 100);
  return risk === "high" ? `Often doesn't show: ${n}% chance it won't come` : `${n}% chance it won't come`;
}

export function cap(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

export function distance(m: number): string {
  return m < 1000 ? `${Math.round(m / 10) * 10} m` : `${(m / 1000).toFixed(1)} km`;
}
