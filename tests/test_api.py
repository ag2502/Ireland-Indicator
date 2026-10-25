import pytest
from fastapi.testclient import TestClient

from api import main
from common import db
from ml.score import Scorer
from pipeline import scores
from tests.gtfs_fixture import make_zip  # noqa: F401
from tests.test_arrivals import DAY, START, world  # noqa: F401  (module-scoped fixture)

NOW = START + 100 * 60


@pytest.fixture(scope="module")
def client(world):  # noqa: F811
    sim, arrivals, outcomes = world
    eng = sim.engine
    db.bulk_load_df(eng, db.arrivals, arrivals)
    db.bulk_load_df(eng, db.trip_outcomes, outcomes)
    arr, out, se = scores.load_day(eng, DAY)
    db.upsert_rows(eng, db.route_scores_daily, scores.day_scores(se, arr, out, DAY).to_dict("records"),
                   keys=["service_date", "route_id", "direction_id"])  # fmt: skip
    db.replace_table(eng, db.route_heatmap, scores.heatmap(arr).to_dict("records"))
    scorer = Scorer(eng)
    scorer._models_checked_at = float("inf")
    scorer.write(scorer.score(NOW))
    db.insert_rows(eng, db.poll_log, [{"polled_at": NOW, "feed": "trip_updates", "ok": True}])
    mp = pytest.MonkeyPatch()
    mp.setattr(main, "now_epoch", lambda: NOW)
    mp.setattr(main, "_since", lambda days: "20261101")
    mp.setattr(main, "MIN_TRIPS_FOR_RANK", 1)
    yield TestClient(main.create_app(eng))
    mp.undo()


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["status"] == "ok"
    assert body["schedule_version"]


def test_stops_list_and_search(client):
    all_stops = client.get("/v1/stops").json()
    assert len(all_stops) == 4
    assert "max-age=86400" in client.get("/v1/stops").headers["cache-control"]
    hit = client.get("/v1/stops", params={"q": all_stops[1]["stop_name"][:5]}).json()
    assert hit and all(all_stops[1]["stop_name"][:5].lower() in s["stop_name"].lower() for s in hit)


def test_stop_detail_by_code_lists_routes(client):
    s = client.get("/v1/stops").json()[0]
    d = client.get(f"/v1/stops/{s['stop_code']}").json()
    assert d["stop_id"] == s["stop_id"]
    assert [r["route_short_name"] for r in d["routes"]] == ["39A"]
    assert client.get("/v1/stops/nope").status_code == 404


def test_arrivals_keep_official_unaltered(client, world):  # noqa: F811
    stops = client.get("/v1/stops").json()
    board = {}
    for s in stops:
        body = client.get(f"/v1/stops/{s['stop_id']}/arrivals", params={"walk_min": 5}).json()
        assert body["attribution"].startswith("Unofficial")
        assert body["stale"] is False
        for a in body["arrivals"]:
            board[(a["trip_id"], s["stop_id"])] = a
    assert board
    import pandas as pd

    live = pd.read_sql("SELECT trip_id, stop_id, official_at FROM live_board", world[0].engine)
    for r in live.itertuples():
        a = board[(r.trip_id, r.stop_id)]
        assert a["official_at"] == r.official_at
        assert a["likely_at"] == r.official_at  # no model loaded: likely = official
        assert a["leave_by_at"] == r.official_at - 300


def test_route_reliability_and_leaderboard(client):
    r = client.get("/v1/routes/39a/reliability", params={"days": 7})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["route"] == "39A"
    assert body["summary"]["n_trips"] > 0
    assert 0 <= body["summary"]["reliability_pct"] <= 100
    assert body["heatmap"]
    lb = client.get("/v1/leaderboard").json()
    assert lb["routes"] and lb["routes"][0]["rank"] == 1
    assert client.get("/v1/routes/XYZ/reliability").status_code == 404
    assert [x["route_short_name"] for x in client.get("/v1/routes").json()] == ["39A"]


def test_vehicles_alerts_accuracy_shapes(client):
    assert client.get("/v1/vehicles").status_code == 200
    al = client.get("/v1/alerts").json()
    assert "routes" in al
    acc = client.get("/v1/accuracy").json()
    assert acc["summary"] is None and acc["daily"] == []


def test_openapi_docs(client):
    spec = client.get("/openapi.json").json()
    for path in ["/v1/stops", "/v1/stops/{stop}/arrivals", "/v1/routes/{route}/reliability", "/v1/leaderboard",
                 "/v1/vehicles", "/v1/accuracy", "/healthz"]:  # fmt: skip
        assert path in spec["paths"]
