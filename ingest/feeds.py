"""Parse GTFS-Realtime FeedMessages into rows for trip_updates and vehicle_positions."""

from __future__ import annotations

from typing import Any

from google.transit import gtfs_realtime_pb2 as rt

TRIP_REL = {v: k for k, v in rt.TripDescriptor.ScheduleRelationship.items()}
STOP_REL = {v: k for k, v in rt.TripUpdate.StopTimeUpdate.ScheduleRelationship.items()}

FEEDS = {"trip_updates": "TripUpdates", "vehicles": "Vehicles"}


def decode(data: bytes) -> rt.FeedMessage:
    msg = rt.FeedMessage()
    msg.ParseFromString(data)
    return msg


def _trip_fields(td: rt.TripDescriptor) -> dict[str, Any]:
    return {
        "trip_id": td.trip_id or None,
        "route_id": td.route_id or None,
        "start_date": td.start_date or None,
        "start_time": td.start_time or None,
        "direction_id": td.direction_id if td.HasField("direction_id") else None,
    }


def trip_update_rows(msg: rt.FeedMessage, polled_at: int, keep_routes: set[str] | None = None) -> list[dict]:
    """One row per stop_time_update; a cancelled trip with no updates still gets one row
    (stop fields NULL) so the cancellation is recorded."""
    rows: list[dict] = []
    for ent in msg.entity:
        if not ent.HasField("trip_update"):
            continue
        tu = ent.trip_update
        trip = _trip_fields(tu.trip)
        if not trip["trip_id"] or (keep_routes is not None and trip["route_id"] not in keep_routes):
            continue
        base = {
            "polled_at": polled_at,
            "trip_at": tu.timestamp or None,
            "vehicle_id": tu.vehicle.id or None,
            "trip_rel": TRIP_REL.get(tu.trip.schedule_relationship, "SCHEDULED"),
            **trip,
        }
        if not tu.stop_time_update:
            rows.append({**base, "stop_id": None, "stop_sequence": None, "stop_rel": None})
            continue
        for stu in tu.stop_time_update:
            arr = stu.arrival if stu.HasField("arrival") else None
            dep = stu.departure if stu.HasField("departure") else None
            rows.append(
                {
                    **base,
                    "stop_id": stu.stop_id or None,
                    "stop_sequence": stu.stop_sequence if stu.HasField("stop_sequence") else None,
                    "stop_rel": STOP_REL.get(stu.schedule_relationship, "SCHEDULED"),
                    "arrival_delay": arr.delay if arr is not None and arr.HasField("delay") else None,
                    "arrival_at": arr.time if arr is not None and arr.HasField("time") else None,
                    "departure_delay": dep.delay if dep is not None and dep.HasField("delay") else None,
                    "departure_at": dep.time if dep is not None and dep.HasField("time") else None,
                }
            )
    return rows


def vehicle_rows(msg: rt.FeedMessage, polled_at: int, keep_routes: set[str] | None = None) -> list[dict]:
    rows: list[dict] = []
    for ent in msg.entity:
        if not ent.HasField("vehicle"):
            continue
        vp = ent.vehicle
        trip = _trip_fields(vp.trip)
        if keep_routes is not None and trip["route_id"] not in keep_routes:
            continue
        if not vp.HasField("position"):
            continue
        rows.append(
            {
                "polled_at": polled_at,
                "ping_at": vp.timestamp or None,
                "vehicle_id": vp.vehicle.id or None,
                "lat": vp.position.latitude,
                "lon": vp.position.longitude,
                "bearing": vp.position.bearing if vp.position.HasField("bearing") else None,
                "speed": vp.position.speed if vp.position.HasField("speed") else None,
                **trip,
            }
        )
    return rows


def parse(feed: str, data: bytes, polled_at: int, keep_routes: set[str] | None = None) -> tuple[list[dict], int, int]:
    """Returns (rows, n_entities, header timestamp)."""
    msg = decode(data)
    fn = trip_update_rows if feed == "trip_updates" else vehicle_rows
    return fn(msg, polled_at, keep_routes), len(msg.entity), msg.header.timestamp
