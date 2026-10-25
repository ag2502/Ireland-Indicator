"""Geometry in Irish Transverse Mercator (EPSG:2157), where one unit is one metre."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from pyproj import Transformer
from shapely.geometry import LineString, Point

_TO_ITM = Transformer.from_crs("EPSG:4326", "EPSG:2157", always_xy=True)


def to_itm(lon: float | np.ndarray, lat: float | np.ndarray) -> tuple:
    return _TO_ITM.transform(lon, lat)


def shape_line(coords_lonlat: Sequence[Sequence[float]]) -> LineString:
    arr = np.asarray(coords_lonlat, dtype=float)
    x, y = to_itm(arr[:, 0], arr[:, 1])
    return LineString(np.column_stack([x, y]))


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return float(2 * r * np.arcsin(np.sqrt(a)))


def project_stops_in_order(
    line: LineString, stops_lonlat: Sequence[tuple[float, float]], backtrack_m: float = 50.0
) -> list[float]:
    """Distance along the line of each stop, forced to be non-decreasing.

    Routes that loop or run both ways along a road make a plain projection jump to the
    wrong pass. Projecting each stop onto the part of the line after the previous stop
    (less a small backtrack allowance for stops that share a pole) avoids that.
    """
    out: list[float] = []
    prev = 0.0
    length = line.length
    xs, ys = to_itm(np.array([s[0] for s in stops_lonlat]), np.array([s[1] for s in stops_lonlat]))
    coords = np.asarray(line.coords)
    cum = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(coords, axis=0).T))])
    for x, y in zip(np.atleast_1d(xs), np.atleast_1d(ys), strict=True):
        start = max(prev - backtrack_m, 0.0)
        # vertices from `start` onward, with the interpolated start point prepended
        i = int(np.searchsorted(cum, start, side="right"))
        sub_pts = [line.interpolate(start).coords[0], *coords[i:]]
        if len(sub_pts) < 2:
            d = length
        else:
            sub = LineString(sub_pts)
            d = start + sub.project(Point(x, y))
        d = max(d, prev)
        out.append(round(float(d), 1))
        prev = d
    return out


def project_point(line: LineString, lon: float, lat: float) -> tuple[float, float]:
    """(distance along line, perpendicular offset), both in metres."""
    x, y = to_itm(lon, lat)
    p = Point(x, y)
    d = line.project(p)
    return float(d), float(line.interpolate(d).distance(p))


def point_at(line: LineString, dist_m: float) -> tuple[float, float]:
    """Inverse of project: lon/lat of the point dist_m along the line."""
    p = line.interpolate(dist_m)
    lon, lat = _FROM_ITM.transform(p.x, p.y)
    return float(lon), float(lat)


_FROM_ITM = Transformer.from_crs("EPSG:2157", "EPSG:4326", always_xy=True)
