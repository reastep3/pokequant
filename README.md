# PokéQuant — quantitative terminal for Pokémon TCG slabs & sealed product

FastAPI + PostgreSQL/TimescaleDB + Redis backend, Next.js Bloomberg-style frontend, deployed as two Fly.io
apps from one multi-stage Dockerfile. The Alpha Module (`backend/app/quant/`) implements grading-arbitrage
spreads, Kelly sizing, sealed-product EV, kernel-regression fair value, a Campbell–Shiller present-value
decomposition and a PyTorch DQN scaffold. See [ARCHITECTURE.md](ARCHITECTURE.md) for the design.

## Quick start (no Docker, no Postgres needed)

```bash
make setup     # python venv + npm ci
make seed      # synthetic EN/JP card pairs + sealed SKUs -> SQLite (backend/dev.db), runs analytics
make api       # http://localhost:8000  (docs at /docs)
make web       # http://localhost:3000
make test      # 42 backend tests (quant math + end-to-end API against the seeded SQLite)
```

Without oracle keys the stack runs in **demo mode** (`/ready` reports `demo_mode: true`): the
`SyntheticOracle` generates GBM price paths with speculative regimes, lognormal sale noise and growing
populations so every screen is populated.

## Configuration

Copy `.env.example` to `backend/.env`. Key settings:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://…` in prod (a `postgres://` URL from `fly postgres attach` is rewritten automatically); SQLite fallback for local dev |
| `REDIS_URL` | screener cache, SSE fan-out, worker job locks. Unset = in-process fallback |
| `API_KEY` | `X-API-Key` for `/api/v1/admin/*` (trigger jobs, inspect ingestion audit) |
| `PPT_API_KEY`, `PARSEBOT_API_KEY` | PokemonPriceTracker and Parse.bot PSA oracles; leaving both empty enables demo mode |
| `GRADING_FEE_USD`, `GRADING_SHIPPING_USD`, `MARKETPLACE_FEE_PCT` | grading-arb and Kelly economics |
| `KELLY_MULTIPLIER`, `KELLY_FRACTION_CAP` | half-Kelly and 25% single-position cap by default |
| `*_INTERVAL_S` | worker cadence for price ingest (daily), pop reports (6h), analytics (hourly) |

## API

| Route | What |
|---|---|
| `GET /api/v1/screener?sort=&asset_type=&language=&limit=` | ranked universe (latest snapshot per asset), cached |
| `GET /api/v1/stream/screener` | Server-Sent Events: pushes a refreshed screener after each analytics run |
| `GET /api/v1/assets/{id}` | full analytics payload: fair-value curve, drawdowns, arb, Kelly, pairs, Campbell–Shiller, pop |
| `GET /api/v1/assets/{id}/prices?grade=PSA10&days=365` | daily bars for charts |
| `GET /api/v1/sealed/ev` | sealed EV matrix |
| `GET /api/v1/signals` | active signals (grading arb, JP/EN pairs, sealed EV, bubble, drawdown-recovery) |
| `POST /api/v1/admin/jobs/{price_ingest\|pop_ingest\|analytics}` | run a job now (`X-API-Key`) |
| `GET /health`, `GET /ready` | liveness / readiness (DB + cache) |

## Deploying to Fly.io

Nothing has been deployed yet. Review, then:

```bash
# 1. Postgres — managed (no timescaledb) …
fly postgres create --name pokequant-db --region iad --vm-size shared-cpu-2x --volume-size 10
fly apps create pokequant-api && fly postgres attach pokequant-db -a pokequant-api
#    … or self-hosted TimescaleDB on a volume (hypertables + compression): see infra/postgres/fly.toml

# 2. Redis (Upstash) + secrets + private LB address + worker volume
fly redis create --name pokequant-cache --region iad
fly secrets set -a pokequant-api REDIS_URL=redis://… API_KEY=$(openssl rand -hex 24) PPT_API_KEY=… PARSEBOT_API_KEY=…
fly ips allocate-v6 --private -a pokequant-api
fly volumes create pokequant_models --region iad --size 3 -a pokequant-api

# 3. Deploy API + worker (release_command runs `alembic upgrade head`), then the web app
fly deploy -c fly.toml
fly apps create pokequant-web && fly deploy -c fly.web.toml
```

The Next.js server reaches the API over Fly's private network (`http://pokequant-api.flycast`); browsers use
`https://pokequant-api.fly.dev` (baked in at build time via `NEXT_PUBLIC_API_URL`). Update `CORS_ORIGINS`
in `fly.toml` if you rename the web app.

## Oracle contracts

Both clients are path-tolerant JSON extractors with configurable endpoints — Parse.bot APIs are
user-defined scrapers, and PokemonPriceTracker's schema should be verified against your plan's docs.
The assumed shapes are documented at the top of `backend/app/ingestion/parsebot_psa.py` and
`backend/app/ingestion/pokemonpricetracker.py`; adjust the `dig(...)` paths there if your payloads differ.

## Layout

```
backend/app/quant/        Alpha Module (pure numpy/scipy; see ARCHITECTURE.md)
backend/app/models/       SQLAlchemy 2.0 schemas (time-series ticks, sales, pop reports, snapshots, signals)
backend/app/ingestion/    oracle clients, JP/EN normalization, synthetic demo oracle, pipeline
backend/app/services/     analytics orchestration + screener reads
backend/app/api/          FastAPI routers (screener, assets, sealed, signals, SSE stream, admin)
backend/app/workers/      asyncio scheduler for the Fly `worker` process group
backend/alembic/          migrations (0001 creates hypertables/compression when TimescaleDB is present)
frontend/src/             Next.js 15 app router terminal UI
Dockerfile, fly.toml, fly.web.toml, infra/postgres/fly.toml, docker-compose.yml
```
