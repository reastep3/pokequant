"""End-to-end: seed synthetic universe into SQLite, run analytics, hit the API."""

import pathlib

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

DB = pathlib.Path(__file__).parent / "test.db"


@pytest.fixture(scope="module", autouse=True)
async def seeded():
    for p in (DB, DB.with_suffix(".db-journal")):
        if p.exists():
            p.unlink()
    from app.ingestion.synthetic import SyntheticOracle
    SyntheticOracle.__init__.__defaults__ = (200, 42)  # shorter history keeps the test fast
    from scripts.seed_demo import seed
    await seed()
    yield


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_health_and_ready(client):
    assert (await client.get("/health")).json()["status"] == "ok"
    r = (await client.get("/ready")).json()
    assert r["db"] == "ok" and r["demo_mode"] is True


async def test_screener_ranks_and_filters(client):
    r = (await client.get("/api/v1/screener?limit=50")).json()
    assert r["count"] > 10
    scores = [row["screener_score"] for row in r["rows"] if row["screener_score"] is not None]
    assert scores == sorted(scores, reverse=True)
    row = r["rows"][0]
    for k in ("dd_30d", "dd_60d", "dd_90d", "kelly_adj_roi", "liquidity_score", "ev_gap_pct"):
        assert k in row
    ja = (await client.get("/api/v1/screener?language=ja")).json()
    assert all(x["language"] == "ja" for x in ja["rows"]) and ja["count"] > 0
    sealed = (await client.get("/api/v1/screener?asset_type=sealed&sort=sealed_ev_ratio")).json()
    assert all(x["grade_key"] == "SEALED" for x in sealed["rows"]) and sealed["count"] > 0


async def test_asset_detail_and_prices(client):
    r = (await client.get("/api/v1/screener?asset_type=card&limit=1")).json()
    aid = r["rows"][0]["id"]
    d = (await client.get(f"/api/v1/assets/{aid}")).json()
    a = d["analytics"]
    assert a["grade_key"] == "PSA10" and a["payload"]["fair_value"]["method"] in {"local_linear_robust", "median_fallback"}
    assert "grading_arb" in a["payload"] and "kelly_acquisition" in a["payload"]
    assert a["recovery_90d_days"] is None or a["recovery_90d_days"] >= 0
    bars = (await client.get(f"/api/v1/assets/{aid}/prices?grade=PSA10&days=90")).json()["bars"]
    assert len(bars) > 30 and bars[-1]["value"] > 0
    assert (await client.get("/api/v1/assets/999999")).status_code == 404


async def test_sealed_matrix_and_signals(client):
    m = (await client.get("/api/v1/sealed/ev")).json()
    assert m["count"] >= 5 and all(r["sealed_ev"] > 0 for r in m["rows"])
    assert m["rows"][0]["ev"]["slot_contributions"]
    s = (await client.get("/api/v1/signals")).json()
    assert "rows" in s


async def test_admin_requires_key_and_runs_job(client):
    assert (await client.post("/api/v1/admin/jobs/analytics")).status_code == 401
    r = await client.post("/api/v1/admin/jobs/analytics", headers={"X-API-Key": "test-key"})
    assert r.status_code == 200 and r.json()["rows"] > 0
    runs = (await client.get("/api/v1/admin/runs", headers={"X-API-Key": "test-key"})).json()["rows"]
    assert runs and runs[0]["status"] == "ok"
