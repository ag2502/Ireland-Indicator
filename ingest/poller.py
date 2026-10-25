"""The only process that talks to the NTA.

Fair usage: one realtime call per key per 60 s. With one key we alternate TripUpdates and
Vehicles, so each refreshes every 2 minutes. With BT_USE_BOTH_KEYS (only after the NTA
agrees) each key polls one feed every minute. On 429/5xx we back off exponentially and
record the gap so labels and scores can skip it.
"""

from __future__ import annotations

import argparse
import gzip
import logging
import time
from dataclasses import dataclass, field

import httpx
from sqlalchemy import Engine, select

from common import db, storage
from common.config import Settings, get_settings
from common.timeutil import now_epoch, to_utc
from ingest import feeds

log = logging.getLogger(__name__)

MAX_BACKOFF_S = 15 * 60


@dataclass
class FeedState:
    last_ok_at: int | None = None
    failures: int = 0
    next_allowed_at: float = 0.0


@dataclass
class Poller:
    engine: Engine
    settings: Settings
    client: httpx.Client | None = None
    simulator: object | None = None
    archive: bool = True
    state: dict[str, FeedState] = field(default_factory=lambda: {f: FeedState() for f in feeds.FEEDS})
    keep_routes: set[str] | None = None
    _turn: int = 0

    def __post_init__(self) -> None:
        if self.keep_routes is None:
            with self.engine.connect() as conn:
                ids = conn.execute(select(db.gtfs_routes.c.route_id).distinct()).scalars().all()
            self.keep_routes = set(ids) or None  # store only our operators' routes

    # ---------------------------------------------------------------- one call
    def fetch(self, feed: str, key_index: int, t: int) -> tuple[int, bytes]:
        if self.settings.feed_mode == "simulated":
            assert self.simulator is not None
            return 200, self.simulator.feed(feed, t)  # type: ignore[attr-defined]
        assert self.client is not None
        key = self.settings.nta_api_keys[key_index]
        url = f"{self.settings.nta_base_url}/{feeds.FEEDS[feed]}"
        resp = self.client.get(url, headers={"x-api-key": key, "Cache-Control": "no-cache"})
        return resp.status_code, resp.content

    def poll_once(self, feed: str, key_index: int = 0, t: int | None = None) -> bool:
        t = t if t is not None else now_epoch()
        st = self.state[feed]
        started = time.monotonic()
        status, body, error = None, b"", None
        try:
            status, body = self.fetch(feed, key_index, t)
        except httpx.HTTPError as exc:
            error = f"{type(exc).__name__}: {exc}"
        latency_ms = int((time.monotonic() - started) * 1000)
        ok = status == 200 and bool(body)
        n_entities, header_at, n_rows = None, None, 0
        if ok:
            try:
                rows, n_entities, header_at = feeds.parse(feed, body, t, self.keep_routes)
                table = db.trip_updates if feed == "trip_updates" else db.vehicle_positions
                n_rows = db.insert_rows(self.engine, table, rows)
                if self.archive:
                    key = f"{feed}/{to_utc(t):%Y/%m/%d/%H%M%S}.pb.gz"
                    storage.put_bytes(self.settings.raw_archive, key, gzip.compress(body, 6))
            except Exception as exc:  # a bad payload must not kill the poller
                ok, error = False, f"parse: {type(exc).__name__}: {exc}"
                log.exception("failed to handle %s payload", feed)
        elif error is None:
            error = f"HTTP {status}: {body[:200]!r}"

        db.insert_rows(
            self.engine,
            db.poll_log,
            [
                {
                    "polled_at": t,
                    "feed": feed,
                    "key_index": key_index,
                    "status_code": status,
                    "ok": ok,
                    "n_entities": n_entities,
                    "n_bytes": len(body),
                    "latency_ms": latency_ms,
                    "header_at": header_at,
                    "error": error,
                }
            ],
        )
        if ok:
            self._record_gap(feed, st.last_ok_at, t)
            st.last_ok_at, st.failures, st.next_allowed_at = t, 0, 0.0
            log.info("%s ok: %s entities, %s rows, %d ms", feed, n_entities, n_rows, latency_ms)
        else:
            st.failures += 1
            if status == 429 or status is None or status >= 500:
                wait = min(MAX_BACKOFF_S, self.settings.poll_interval_s * 2**st.failures)
                st.next_allowed_at = time.monotonic() + wait
                log.warning("%s failed (%s); backing off %d s", feed, error, wait)
            else:
                log.error("%s failed: %s", feed, error)
        return ok

    def _record_gap(self, feed: str, prev_ok: int | None, now: int) -> None:
        """A gap is anything longer than twice the feed's normal refresh interval."""
        if prev_ok is None:
            return
        expected = self.settings.poll_interval_s * (1 if self.settings.use_both_keys else 2)
        if now - prev_ok > 2 * expected + 15:
            db.insert_rows(
                self.engine,
                db.feed_gaps,
                [{"feed": feed, "start_at": prev_ok, "end_at": now, "reason": "poll gap"}],
            )
            log.warning("%s gap of %d s recorded", feed, now - prev_ok)

    # ---------------------------------------------------------------- schedule
    def plan_tick(self) -> list[tuple[str, int]]:
        """Which (feed, key_index) calls to make this minute."""
        if self.settings.use_both_keys and len(self.settings.nta_api_keys) >= 2:
            return [("trip_updates", 0), ("vehicles", 1)]
        feed = ("trip_updates", "vehicles")[self._turn % 2]
        self._turn += 1
        return [(feed, 0)]

    def heartbeat(self) -> None:
        if not self.settings.heartbeat_url:
            return
        try:
            httpx.get(self.settings.heartbeat_url, timeout=5)
        except httpx.HTTPError:
            log.warning("heartbeat ping failed")

    def run_forever(self) -> None:
        interval = self.settings.poll_interval_s
        next_tick = time.monotonic()
        while True:
            any_ok = False
            for feed, key_index in self.plan_tick():
                if time.monotonic() < self.state[feed].next_allowed_at:
                    continue
                any_ok |= self.poll_once(feed, key_index)
            if any_ok:
                self.heartbeat()
            next_tick += interval
            time.sleep(max(0.0, next_tick - time.monotonic()))


def build(settings: Settings | None = None, engine: Engine | None = None) -> Poller:
    settings = settings or get_settings()
    engine = engine or db.init_db()
    if settings.feed_mode == "nta":
        if not settings.nta_api_keys:
            raise SystemExit("BT_NTA_API_KEYS is empty; set it or use BT_FEED_MODE=simulated")
        return Poller(engine, settings, client=httpx.Client(timeout=30))
    from ingest.simulator import make_simulator

    sim = make_simulator(engine)
    poller = Poller(engine, settings, simulator=sim)
    return poller


def run(argv: list[str] | None = None) -> int:
    """CLI: bustrust poll [--once]"""
    p = argparse.ArgumentParser(prog="bustrust poll")
    p.add_argument("--once", action="store_true", help="poll each feed once and exit")
    args = p.parse_args(argv)
    poller = build()
    log.info("poller starting in %s mode", poller.settings.feed_mode)
    if args.once:
        ok = all(poller.poll_once(f) for f in feeds.FEEDS)
        return 0 if ok else 1
    poller.run_forever()
    return 0
