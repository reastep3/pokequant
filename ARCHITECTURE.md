# Architecture

## System

```
                 ┌────────────────────────────┐        ┌──────────────────────────────┐
  browser ─────▶ │ pokequant-web (Next.js 15)  │ ──6PN─▶│ pokequant-api  [api] uvicorn  │◀── browser (SSE, JSON)
                 │ SSR via API_INTERNAL_URL    │        │   FastAPI · /api/v1 · /ready  │
                 └────────────────────────────┘        │ pokequant-api  [worker]       │
                                                        │   asyncio scheduler + locks   │
                                                        └───────┬───────────┬──────────┘
                                                                │           │
                                              ┌─────────────────▼──┐   ┌────▼──────────────┐
                                              │ Postgres/Timescale │   │ Redis (Upstash)    │
                                              │ hypertables, BRIN, │   │ screener cache,    │
                                              │ compression, cagg  │   │ pub/sub, job locks │
                                              └────────────────────┘   └───────────────────┘
        oracles: PokemonPriceTracker (raw + graded aggregates) · Parse.bot PSA (pop reports, auction lots)
```

One `Dockerfile`, two build targets (`api`, `web`), three Fly configs. The API app runs two process
groups from the same image: `api` (HTTP, SSE) and `worker` (ingestion + analytics). Rolling deploys with
`release_command = "alembic upgrade head"`. The worker mounts a volume for DQN checkpoints.

## Data model (`backend/app/models`)

| Table | Grain | Notes |
|---|---|---|
| `card_sets` | set | JP sets carry `en_equivalent_code` |
| `assets` | card print or sealed SKU | `ticker`, `canonical_key` (joins JP↔EN prints), `external_ids` per oracle |
| `print_equivalence` | JP→EN mapping | identical / reprint / promo_exclusive / no_equivalent + confidence |
| `price_ticks` | asset × grade × source × ts | daily bars; **hypertable**, 7-day chunks, columnar compression after 30d |
| `sales` | individual lots | feeds kernel regression; unique on (source, external_id); hypertable |
| `pop_reports` | asset × grader × ts | full snapshot each scrape → velocity is a read-side diff |
| `sealed_products`, `pull_rates` | SKU / set slot | slot = specific chase card or rarity tier expanded from member RAW prices |
| `analytics_snapshots` | asset × grade × run | denormalized engine output; screener = latest row per key (index-only) |
| `signals` | event | refreshed every analytics run: updated if persisting, expired otherwise |
| `ingestion_runs` | job run | audit trail (status, rows, error) |

`PortableJSON` (JSONB on PG) and `TZDateTime` (TIMESTAMPTZ on PG, UTC-normalized on SQLite) keep the
same models valid for tests on SQLite. On plain Postgres without TimescaleDB the migration falls back to
BRIN + a partial "recent" index.

## Ingestion (`backend/app/ingestion`)

* `base.py` – `OracleClient` (httpx, token-bucket RPS, exponential backoff on 429/5xx, `Retry-After`),
  typed `PricePoint` / `SaleRecord` / `PopSnapshot`, tolerant `dig()` JSON extraction.
* `pokemonpricetracker.py` – RAW (TCGplayer market) + graded (eBay PSA/BGS/CGC) aggregates per card.
* `parsebot_psa.py` – PSA pop reports (normalized grade labels) and auction lots per grade.
* `normalization.py` – `canonical_key` + `resolve_ja_to_en()`: curated overrides → mapped-set match on
  number/rarity class → promo exclusivity (nearest EN print as proxy, else no equivalent) → reprint.
  Tables live in `seed/print_equivalence.json` (set map incl. split JP sets like s7r+s7d→swsh7, rarity
  map SAR→SIR / AR→IR, romanized name aliases, promo overrides such as Yu Nagaba ↔ Van Gogh Pikachu).
* `pipeline.py` – idempotent upserts (`ON CONFLICT` on PG and SQLite), lot→daily-bar aggregation,
  audit rows. `synthetic.py` is the demo oracle.
* Population velocity = Δtotal/Δt over trailing 30d; momentum z vs the prior six 30-day windows.

## Alpha Module (`backend/app/quant`)

| Module | Method |
|---|---|
| `kelly.py` | `kelly_discrete`: exact growth-optimal f for a discrete outcome distribution (Brent root of G'(f) on [0, ruin bound]); `slab_acquisition_kelly` (raw→graded, probabilities from Dirichlet-smoothed pop with a condition tilt for submitter selection bias); `slab_holding_kelly` (Merton f*=μ/σ² with mean reversion to fair value over the horizon); `kelly_portfolio` (long-only, gross-capped QP). Half-Kelly and a 25% cap by default. |
| `arbitrage.py` | Grading spread = E[net graded value] − (raw + fee + shipping), breakeven gem rate, no-look-ahead rolling z-score signal; JP/EN pairs: OLS hedge ratio, AR(1) mean-reversion λ, half-life = −ln2/λ. |
| `sealed_ev.py` | Compound-binomial EV/variance per pull slot (hits ~ Bin(packs, p), value drawn from member cards), fees + liquidity haircut, Monte Carlo P(profit) and percentiles, ranked EV matrix. |
| `kernel_regression.py` | Robust local-linear Gaussian-kernel regression in log space (boundary-unbiased at "now"), LOOCV bandwidth over Silverman multiples with a 7-day floor, two Tukey-biweight reweighting passes, SE from equivalent-kernel weights, median fallback for < 5 sales. |
| `campbell_shiller.py` | Log-linear PV identity with ρ pinned by an explicit fundamental-yield assumption (the scarcity/nostalgia flow proxy `log(turnover/pop) + κ·log(1+age)` has no identified level). Default estimator: Cochrane long-run regressions (b_d, b_r, φ) with t-stat shrinkage and share caps — stable in 18-month samples; unrestricted ridge-VAR(1) for ≥ 60 obs. Outputs cash-flow / discount-rate / rational-bubble variance shares (sum to 1), fundamental value, and the **speculative premium** = deviation from the constant-required-return fundamental. |
| `drawdown.py` | 30/60/90-day max drawdown, current drawdown, recovery time (trough → prior peak), underwater days, annualized realized vol on irregular spacing. |
| `liquidity.py` | 0–100 composite: trade frequency, recency, price dispersion, listing depth, bid/ask proxy. |
| `screener.py` | Kelly-adjusted ROI = f_scaled × E[return]; score = KAR × √liq × (1 + ½·EV gap) × (1 + 0.1·arb z) × (1 − ½·bubble). |
| `rl/env.py`, `rl/dqn.py` | `SlabSpreadEnv` (state: arb z, pairs z, pop momentum, liquidity, EV gap, DD30, position, PnL, holding time; actions: hold/buy-and-grade/sell; reward: MTM Δ net of costs) and a Double-DQN agent (replay buffer, target sync, Huber loss). `torch` is an optional extra; the API image does not ship it. |

`quant_engine.py` is the façade: `QuantEngine.analyze_slab(SlabInputs)` / `analyze_sealed(SealedInputs)`
return dicts that map 1:1 onto `analytics_snapshots`. `services/analytics_service.py` loads the rows,
aligns JP/EN pairs on calendar days, builds monthly Campbell–Shiller inputs (per-day turnover, trailing
3-month mean, partial months dropped), writes snapshots and refreshes signals.

## Real-time path

Worker analytics → `screener_service.invalidate()` bumps a cache generation → Redis pub/sub
`analytics:updated` → `/api/v1/stream/screener` (SSE, 15 s keepalive) re-reads the screener with a fresh
session and pushes it → the screener table mutates its SWR cache in place (30 s polling as fallback).

## Testing

`backend/tests`: closed-form checks for Kelly (2p−1), compound-binomial variance, local-linear exactness
on linear data + boundary bias vs Nadaraya–Watson, OU half-life recovery, injected-bubble detection,
drawdown/recovery arithmetic, JP/EN normalization cases, and an end-to-end API test that seeds the
synthetic universe into SQLite and exercises every route.
