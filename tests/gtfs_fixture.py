"""A tiny synthetic GTFS zip: one Dublin Bus route east-west along a 3 km line, four stops,
trips every 10 minutes, one after-midnight trip, plus a non-Dublin route to be filtered."""

from __future__ import annotations

import io
import zipfile

import numpy as np

from common import geo

LAT = 53.35
LON0 = -6.30
# ~3 km east along latitude 53.35
LON1 = LON0 + 3000 / (111_320 * np.cos(np.radians(LAT)))
STOP_FRACS = [0.0, 1 / 3, 2 / 3, 1.0]


def _csv(rows: list[list]) -> str:
    return "\n".join(",".join(str(c) for c in r) for r in rows) + "\n"


def make_zip(n_trips: int = 12, start_s: int = 7 * 3600, headway_s: int = 600) -> bytes:
    shape = [["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"]]
    for i, f in enumerate(np.linspace(0, 1, 31)):
        shape.append(["S1", LAT, round(LON0 + f * (LON1 - LON0), 7), i + 1])
    stops = [["stop_id", "stop_code", "stop_name", "stop_lat", "stop_lon"]]
    for i, f in enumerate(STOP_FRACS):
        # stops sit 10 m north of the road, as real poles do
        stops.append([f"ST{i}", 1000 + i, f"Stop {i}", LAT + 0.00009, LON0 + f * (LON1 - LON0)])
    stops.append(["XX", 9999, "Cork stop", 51.9, -8.47])
    trips = [["route_id", "service_id", "trip_id", "trip_headsign", "direction_id", "block_id", "shape_id"]]
    st = [["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"]]

    def hms(s: int) -> str:
        return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"

    starts = [start_s + k * headway_s for k in range(n_trips)] + [25 * 3600 + 600]
    for k, t0 in enumerate(starts):
        tid = f"T{k}"
        trips.append(["R1", "WK", tid, "East", 0, f"B{k % 3}", "S1"])
        for i in range(4):
            t = t0 + i * 240  # 4 min between stops
            st.append([tid, hms(t), hms(t), f"ST{i}", i + 1])
    trips.append(["R9", "WK", "C1", "Cork", 0, "", ""])
    st.append(["C1", "08:00:00", "08:00:00", "XX", 1])

    files = {
        "agency.txt": _csv(
            [
                ["agency_id", "agency_name", "agency_url", "agency_timezone"],
                ["1", "Dublin Bus", "https://x", "Europe/London"],
                ["2", "Bus Eireann", "https://y", "Europe/London"],
            ]
        ),
        "routes.txt": _csv(
            [
                ["route_id", "agency_id", "route_short_name", "route_long_name", "route_type"],
                ["R1", "1", "39A", "Ongar - UCD", 3],
                ["R9", "2", "220", "Cork", 3],
            ]
        ),
        "trips.txt": _csv(trips),
        "stop_times.txt": _csv(st),
        "stops.txt": _csv(stops),
        "shapes.txt": _csv(shape),
        "calendar.txt": _csv(
            [
                [
                    "service_id",
                    "monday",
                    "tuesday",
                    "wednesday",
                    "thursday",
                    "friday",
                    "saturday",
                    "sunday",
                    "start_date",
                    "end_date",
                ],
                ["WK", 1, 1, 1, 1, 1, 1, 1, "20261001", "20271231"],
            ]
        ),
        "calendar_dates.txt": _csv([["service_id", "date", "exception_type"], ["WK", "20261225", 2]]),
        "feed_info.txt": _csv(
            [
                ["feed_publisher_name", "feed_start_date", "feed_end_date", "feed_version"],
                ["NTA", "20261001", "20271231", "TEST-1"],
            ]
        ),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return buf.getvalue()


def stop_dist(i: int) -> float:
    line = geo.shape_line([[LON0, LAT], [LON1, LAT]])
    return STOP_FRACS[i] * line.length
