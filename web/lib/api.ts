// Types mirror api/schemas.py. The browser calls this origin; Next proxies to FastAPI.

export type Stop = {
  stop_id: string;
  stop_code: string | null;
  stop_name: string | null;
  stop_lat: number;
  stop_lon: number;
};

export type StopDetail = Stop & { routes: { route_id: string; route_short_name: string | null }[] };

export type StopStatus = "on_time" | "late" | "early" | "scheduled" | "cancelled" | "skipped" | "departed";
export type TripStatus = "not_started" | "running" | "diverted" | "cancelled" | "finished";

export type Arrival = {
  trip_id: string;
  route_id: string | null;
  route: string | null;
  headsign: string | null;
  status: StopStatus;
  delay_s: number | null;
  scheduled_at: number | null;
  official_at: number | null;
  official_source: "realtime" | "timetable" | null;
  likely_at: number | null;
  early_at: number | null;
  late_at: number | null;
  passed_at: number | null;
  noshow_prob: number | null;
  noshow_risk: "low" | "medium" | "high" | "cancelled" | null;
  tracked: boolean;
  vehicle_id: string | null;
  position: {
    lat: number | null;
    lon: number | null;
    ping_at: number | null;
    last_stop_id: string | null;
    last_stop_name: string | null;
    stops_away: number | null;
    off_route: boolean;
  };
  leave_by_at: number | null;
};

export type Arrivals = {
  stop: Stop;
  now: number;
  board_made_at: number | null;
  stale: boolean;
  arrivals: Arrival[];
  attribution: string;
};

export type Health = {
  status: string;
  problems: string[];
  now: number;
  last_poll_at: Record<string, number>;
  board_made_at: number | null;
  model_version: string | null;
  schedule_version: string | null;
  feed_mode: "nta" | "simulated";
};

export type Vehicle = {
  vehicle_id: string | null;
  trip_id: string | null;
  route_id: string | null;
  route: string | null;
  direction_id: number | null;
  headsign: string | null;
  status: TripStatus;
  delay_s: number | null;
  lat: number;
  lon: number;
  bearing: number | null;
  ping_at: number | null;
  next_stop_id: string | null;
  off_route: boolean;
};

export type TripStop = {
  stop_sequence: number;
  stop_id: string;
  stop_name: string | null;
  stop_lat: number | null;
  stop_lon: number | null;
  scheduled_at: number;
  state: "passed" | "upcoming" | "skipped" | "cancelled";
  passed_at: number | null;
  official_at: number | null;
  likely_at: number | null;
};

export type Trip = {
  trip_id: string;
  route_id: string | null;
  route: string | null;
  direction_id: number | null;
  headsign: string | null;
  service_date: string;
  status: TripStatus;
  delay_s: number | null;
  vehicle_id: string | null;
  lat: number | null;
  lon: number | null;
  bearing: number | null;
  ping_at: number | null;
  last_stop_id: string | null;
  next_stop_id: string | null;
  off_route: boolean;
  noshow_prob: number | null;
  start_at: number | null;
  end_at: number | null;
  made_at: number;
  stale: boolean;
  stops: TripStop[];
  shape: [number, number][];
  attribution: string;
};

export type AlertTrip = {
  trip_id: string;
  headsign: string | null;
  start_at: number | null;
  status: TripStatus;
  reason: "cancelled" | "diverted" | "at_risk" | "very_late";
  delay_s: number | null;
  noshow_prob: number | null;
};

export type Alerts = {
  now: number;
  routes: { route: string; cancelled: number; diverted: number; at_risk: number; very_late: number; trips: AlertTrip[] }[];
  attribution: string;
};

export type Score = {
  n_trips: number;
  reliability_pct: number | null;
  noshow_pct: number | null;
  on_time_pct: number | null;
  bunched_pct: number | null;
  ewt_s: number | null;
  mean_delay_s: number | null;
};

export type RouteRef = { route_id: string; agency_id: string | null; route_short_name: string | null; route_long_name: string | null };

export type RouteReliability = {
  route: string;
  route_ids: string[];
  long_name: string | null;
  days: number;
  summary: Score;
  by_direction: Record<string, Score>;
  daily: (Score & { service_date: string })[];
  heatmap: { weekday: number; hour: number; n: number; on_time_share: number | null; median_delay_s: number | null }[];
  slowest_segments: {
    direction_id: number;
    from_stop_id: string;
    to_stop_id: string;
    from_name: string | null;
    to_name: string | null;
    n: number;
    median_lost_s: number;
  }[];
  attribution: string;
};

export type Leaderboard = { days: number; routes: (Score & { route: string; rank: number })[]; attribution: string };

export type Accuracy = {
  days: number;
  summary: { n: number; mae_official_s: number; mae_ours_s: number; coverage: number } | null;
  daily: { service_date: string; horizon: string; n: number; mae_official_s: number; mae_ours_s: number; coverage: number }[];
  models: Record<string, { version: string; created_at: number; test_days: string[] | null } | undefined>;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, { signal, headers: { Accept: "application/json" } });
  if (!res.ok && !(res.status === 503 && path === "/healthz")) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {}
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}
