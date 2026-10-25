"""The official prediction, reconstructed exactly as a GTFS-Realtime consumer must.

A TripUpdate carries delays (or absolute times) for some stops. Per the spec, a stop
without its own update inherits the delay of the nearest preceding update on the trip.
For every poll, trip and downstream stop this gives the predicted arrival riders saw on
the official board. Stops before the first update have already been passed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def propagate(tu: pd.DataFrame, sched: pd.DataFrame) -> pd.DataFrame:
    """Expand trip updates to one row per (poll, trip, downstream stop).

    tu: polled_at, trip_id, stop_sequence, arrival_delay, arrival_at, departure_delay,
        trip_rel, stop_rel, vehicle_id (rows from trip_updates for one service date)
    sched: trip_id, stop_sequence, stop_id, scheduled_at (one service date)

    Returns polled_at, trip_id, stop_sequence, stop_id, scheduled_at, official_at,
    official_delay, upd_seq (the update the delay came from), vehicle_id.
    Cancelled trips and stops marked SKIPPED/NO_DATA produce no rows.
    """
    cols = ["polled_at", "trip_id", "stop_sequence", "stop_id", "scheduled_at", "official_at", "official_delay"]
    if tu.empty:
        return pd.DataFrame(columns=[*cols, "upd_seq", "vehicle_id"])
    u = tu[(tu["trip_rel"] != "CANCELED") & tu["stop_sequence"].notna()].copy()
    u["stop_sequence"] = u["stop_sequence"].astype(int)
    # delay from the update: prefer explicit delay, else absolute time minus schedule
    s_key = sched[["trip_id", "stop_sequence", "scheduled_at"]]
    u = u.merge(s_key, on=["trip_id", "stop_sequence"], how="left")
    delay = u["arrival_delay"].astype("float")
    delay = delay.fillna(u["departure_delay"].astype("float"))
    delay = delay.fillna(u["arrival_at"].astype("float") - u["scheduled_at"])
    u = u.assign(delay=delay, upd_seq=u["stop_sequence"])
    no_data = u["stop_rel"].isin(["NO_DATA", "SKIPPED"])
    u = u[["polled_at", "trip_id", "upd_seq", "delay", "vehicle_id"]].assign(blocked=no_data.to_numpy())

    # cross every update with its trip's stops at or after it, keep the nearest update
    m = u.merge(sched[["trip_id", "stop_sequence", "stop_id", "scheduled_at"]], on="trip_id")
    m = m[m["stop_sequence"] >= m["upd_seq"]]
    m = m.sort_values(["polled_at", "trip_id", "stop_sequence", "upd_seq"])
    m = m.drop_duplicates(["polled_at", "trip_id", "stop_sequence"], keep="last")
    m = m[~(m["blocked"] & (m["stop_sequence"] == m["upd_seq"]))]
    m = m[m["delay"].notna()]
    m["official_delay"] = m["delay"].round().astype("int64")
    m["official_at"] = m["scheduled_at"].astype("int64") + m["official_delay"]
    return m[[*cols, "upd_seq", "vehicle_id"]].reset_index(drop=True)


def horizon_bucket(minutes: pd.Series | np.ndarray) -> pd.Series:
    bins = [-np.inf, 5, 10, 20, 40, np.inf]
    labels = ["0-5", "5-10", "10-20", "20-40", "40-60"]
    return pd.cut(pd.Series(minutes), bins=bins, labels=labels, right=False).astype(str)
