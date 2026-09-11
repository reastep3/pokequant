import Link from "next/link";
import { notFound } from "next/navigation";
import { api } from "@/lib/api";
import { PriceChart, type FairCurve } from "@/components/price-chart";
import { StatTile } from "@/components/stat-tile";
import { ArbPanel, DecompositionPanel, DrawdownPanel, KellyPanel, PopPanel, SealedPanel } from "@/components/asset-panels";
import { money, pct, short, tone, z } from "@/lib/format";

export const dynamic = "force-dynamic";

function fairCurve(a: NonNullable<Awaited<ReturnType<typeof api.asset>>["analytics"]>): FairCurve {
  const fv = a.payload.fair_value, s = a.payload.series;
  if (!fv || !s || fv.method !== "local_linear_robust") return [];
  const t0 = new Date(s.t0).getTime();
  const byDay = new Map<string, { time: string; fair: number; lo: number; hi: number }>();
  fv.grid_t.forEach((d, i) => {
    const time = new Date(t0 + Math.round(d) * 86400000).toISOString().slice(0, 10);
    byDay.set(time, { time, fair: fv.fitted[i], lo: fv.lower[i], hi: fv.upper[i] });
  });
  return [...byDay.values()].sort((x, y) => (x.time < y.time ? -1 : 1));
}

export default async function AssetPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let d: Awaited<ReturnType<typeof api.asset>>;
  try { d = await api.asset(id); } catch { notFound(); }
  const a = d.analytics;
  const grade = a?.grade_key ?? "PSA10";
  const bars = (await api.prices(id, grade).catch(() => ({ bars: [] }))).bars;
  const fair = a ? fairCurve(a) : [];
  const asset = d.asset;
  const isSealed = asset.asset_type === "sealed";
  return (
    <div className="p-2 space-y-2">
      <div className="flex items-end gap-4 flex-wrap">
        <div>
          <div className="text-[10px] dim"><Link href="/" className="amber">SCREENER</Link> › {asset.set_name ?? "—"} · #{asset.number ?? "—"} · {asset.rarity ?? "—"}</div>
          <h1 className="text-xl amber tracking-wider">{asset.ticker} <span className="dim text-sm">{asset.name} · {asset.language.toUpperCase()} · {grade}</span></h1>
        </div>
        <div className="ml-auto flex gap-2 flex-wrap">
          <StatTile k="Market" v={money(a?.market_price)} s={<span className={tone(a?.ret_30d)}>{pct(a?.ret_30d)} 30d</span>} />
          <StatTile k={isSealed ? "Sealed EV" : "Fair value (kernel)"} v={money(a?.fair_value)} s={a?.fair_value_lo != null ? `${money(a.fair_value_lo)} – ${money(a.fair_value_hi)}` : undefined} />
          <StatTile k="EV gap" v={pct(a?.ev_gap_pct)} tone={tone(a?.ev_gap_pct)} s={a?.payload.fair_value?.z_market_vs_fair != null ? `market ${z(a.payload.fair_value.z_market_vs_fair)} vs fair` : undefined} />
          <StatTile k="Kelly f" v={pct(a?.kelly_fraction_scaled, 1, false)} s={`f* ${pct(a?.kelly_fraction, 1, false)} · K-ROI ${pct(a?.kelly_adj_roi, 2)}`} />
          <StatTile k="Liquidity" v={a?.liquidity_score?.toFixed(0) ?? "—"} s="0–100 composite" />
          <StatTile k="90d drawdown" v={pct(a?.dd_90d, 0, false)} tone="down" s={a?.dd_90d && a.dd_90d < 0 ? `recovery ${a.recovery_90d_days == null ? "open" : a.recovery_90d_days + "d"}` : "no drawdown"} />
        </div>
      </div>

      {!a && <div className="panel p-3 muted">No analytics snapshot yet — run the analytics job.</div>}

      <section className="panel">
        <div className="panel-h"><span>{grade} price · daily bars {bars.length ? `${bars[0].time} → ${bars[bars.length - 1].time}` : ""}</span><span className="normal-case tracking-normal dim">{a?.payload.fair_value ? `bandwidth ${a.payload.fair_value.bandwidth_days.toFixed(0)}d · n=${a.payload.fair_value.n_obs} · ${a.payload.fair_value.method}` : ""}</span></div>
        {bars.length ? <PriceChart bars={bars} fair={fair} fundamental={a?.cs_fundamental_value ?? null} /> : <div className="p-3 muted">No price history.</div>}
      </section>

      {a && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-2">
          <DrawdownPanel a={a} />
          {isSealed ? <SealedPanel a={a} /> : <ArbPanel a={a} />}
          <KellyPanel a={a} />
          {isSealed ? null : <DecompositionPanel a={a} />}
          {isSealed ? null : <PopPanel a={a} />}
          <section className="panel">
            <div className="panel-h"><span>Active signals</span><span className="normal-case tracking-normal dim">snapshot {short(a.ts)}</span></div>
            <div className="panel-b">
              {d.signals.length === 0 && <span className="muted">None.</span>}
              {d.signals.map((s) => (
                <div key={s.id} className="flex gap-2 items-center py-0.5 text-[11px]">
                  <span className={`tag ${s.direction}`}>{s.direction.toUpperCase()}</span>
                  <span className="dim w-28">{s.kind}</span><span>{s.headline}</span>
                </div>
              ))}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
