import type { AssetDetail, DrawdownStat } from "@/lib/types";
import { KV, Panel } from "@/components/stat-tile";
import { days, intf, money, num, pct, tone, z } from "@/lib/format";

type A = NonNullable<AssetDetail["analytics"]>;

export function DrawdownPanel({ a }: { a: A }) {
  const dd = a.payload.drawdowns ?? {};
  const rows: [string, DrawdownStat | undefined][] = [["30d", dd["30"]], ["60d", dd["60"]], ["90d", dd["90"]]];
  return (
    <Panel title="Drawdown & recovery">
      <table className="grid-table">
        <thead><tr><th>Window</th><th className="num">Max DD</th><th className="num">Now</th><th className="num">Recovery</th><th className="num">Underwater</th></tr></thead>
        <tbody>
          {rows.map(([w, s]) => (
            <tr key={w}>
              <td>{w}</td>
              <td className="num down tnum">{pct(s?.max_drawdown, 1, false)}</td>
              <td className={`num tnum ${tone(s?.current_drawdown)}`}>{pct(s?.current_drawdown, 1, false)}</td>
              <td className="num tnum">{s && s.max_drawdown < 0 ? days(s.recovery_days) : "—"}</td>
              <td className="num tnum dim">{s ? `${s.underwater_days}d` : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-2 text-[10.5px] muted">Recovery = days from trough to first close at/above the pre-drawdown peak. “open” = not yet recovered. 90d realized vol {pct(a.vol_90d_ann, 0, false)} ann.</div>
    </Panel>
  );
}

export function ArbPanel({ a }: { a: A }) {
  const g = a.payload.grading_arb;
  const k = a.payload.kelly_acquisition;
  const sig = a.payload.arb_signal;
  if (!g) return <Panel title="Grading arbitrage"><span className="muted">No raw price / pop report for this asset.</span></Panel>;
  const grades = Object.entries(g.per_grade).sort((x, y) => Number(y[0]) - Number(x[0]));
  return (
    <Panel title="Grading arbitrage · raw + fees vs expected slab" right={sig && <span className={`tag ${sig.direction}`}>{sig.direction.toUpperCase()} {z(sig.z)}</span>}>
      <div className="grid grid-cols-2 gap-4">
        <KV rows={[
          ["Raw price", money(g.raw_price)],
          ["All-in cost (raw + grading + ship)", money(g.all_in_cost)],
          ["E[graded value] net of fees", money(g.expected_graded_value)],
          ["Spread", <span key="s" className={tone(g.spread)}>{money(g.spread)} ({pct(g.spread_pct)})</span>],
          ["Breakeven gem rate", pct(g.breakeven_gem_rate, 0, false)],
          ["Spread z vs 90d", z(a.arb_spread_z)],
        ]} />
        <table className="grid-table">
          <thead><tr><th>Grade</th><th className="num">P</th><th className="num">Net</th><th className="num">Return</th></tr></thead>
          <tbody>
            {grades.map(([gr, d]) => (
              <tr key={gr}><td>PSA {gr}</td><td className="num tnum">{pct(d.prob, 0, false)}</td><td className="num tnum">{money(d.net)}</td>
                <td className={`num tnum ${tone(d.net / g.all_in_cost - 1)}`}>{pct(d.net / g.all_in_cost - 1, 0)}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      {k && (
        <div className="mt-3 pt-2 border-t" style={{ borderColor: "var(--border)" }}>
          <div className="text-[10.5px] dim mb-1">KELLY · buy raw, grade, sell slab (discrete outcome distribution, half-Kelly, 25% cap)</div>
          <div className="flex gap-2 flex-wrap">
            <Mini k="f*" v={pct(k.f_star, 1, false)} />
            <Mini k="f scaled" v={pct(k.f_scaled, 1, false)} />
            <Mini k="E[return]" v={pct(k.expected_return, 1)} t={tone(k.expected_return)} />
            <Mini k="growth/bet" v={pct(k.growth_rate, 2)} />
            <Mini k="P(win)" v={pct(k.win_prob, 0, false)} />
            <Mini k="σ" v={pct(Math.sqrt(k.variance), 0, false)} />
          </div>
          {k.note && <div className="muted text-[10px] mt-1">{k.note}</div>}
        </div>
      )}
    </Panel>
  );
}

export function KellyPanel({ a }: { a: A }) {
  const h = a.payload.kelly_holding;
  const l = a.payload.liquidity;
  return (
    <Panel title="Kelly allocation · buy slab at market, 90d horizon">
      <div className="grid grid-cols-2 gap-4">
        <KV rows={[
          ["μ (90d, incl. reversion to fair)", pct(h?.mu_h, 1)],
          ["σ² (90d)", num(h?.sigma2_h, 4)],
          ["f* = μ/σ²", pct(h?.f_star, 1, false)],
          ["Allocation (half-Kelly, capped)", <b key="a" className="amber">{pct(h?.f_scaled, 1, false)} of bankroll</b>],
          ["Expected 90d return", pct(h?.expected_return, 1)],
          ["Kelly-adjusted ROI", pct(a.kelly_adj_roi, 2)],
        ]} />
        <div>
          <div className="text-[10.5px] dim mb-1">LIQUIDITY {num(l?.score, 0)}/100</div>
          {l && Object.entries(l.components).map(([k, v]) => (
            <div key={k} className="flex items-center gap-2 text-[10.5px] py-0.5">
              <span className="w-16 dim">{k}</span>
              <span className="bar flex-1"><span style={{ width: `${v * 100}%`, background: "var(--series-1)" }} /></span>
              <span className="tnum w-8 text-right">{(v * 100).toFixed(0)}</span>
            </div>
          ))}
        </div>
      </div>
    </Panel>
  );
}

export function DecompositionPanel({ a }: { a: A }) {
  const cs = a.payload.campbell_shiller;
  const pr = a.payload.pairs;
  return (
    <Panel title="Campbell–Shiller decomposition · JP/EN pairs">
      {cs ? (
        <div className="grid grid-cols-2 gap-4">
          <KV rows={[
            ["Fundamental value (scarcity × nostalgia)", money(cs.fundamental_value)],
            ["Speculative premium", <span key="b" className={tone(cs.bubble_premium_pct, true)}>{pct(cs.bubble_premium_pct, 0)}</span>],
            ["ρ / κ", `${num(cs.rho, 3)} / ${num(cs.kappa, 3)}`],
            ["VAR obs (monthly)", `${cs.n_obs}${cs.stable ? "" : " · shrunk"}`],
          ]} />
          <div>
            <div className="text-[10.5px] dim mb-1">VARIANCE SHARES OF LOG P/F</div>
            {([["cash-flow (fundamental growth)", cs.var_share_cf, "var(--series-3)"], ["discount-rate (expected return)", cs.var_share_dr, "var(--series-1)"], ["non-fundamental (bubble)", cs.var_share_bubble, "var(--series-2)"]] as const).map(([k, v, c]) => (
              <div key={k} className="flex items-center gap-2 text-[10.5px] py-0.5">
                <span className="w-44 dim">{k}</span>
                <span className="bar flex-1"><span style={{ width: `${Math.min(100, Math.max(0, v * 100))}%`, background: c }} /></span>
                <span className="tnum w-10 text-right">{pct(v, 0)}</span>
              </div>
            ))}
            <Spark values={cs.bubble_series} />
          </div>
        </div>
      ) : <span className="muted">Needs ≥16 months of bars + pop history.</span>}
      <div className="mt-3 pt-2 border-t" style={{ borderColor: "var(--border)" }}>
        {pr ? (
          <div className="flex gap-2 flex-wrap">
            <Mini k="pairs z" v={z(pr.z)} t={Math.abs(pr.z) >= 2 ? "amber" : ""} />
            <Mini k="hedge β" v={num(pr.beta, 2)} />
            <Mini k="half-life" v={pr.half_life_days ? `${pr.half_life_days.toFixed(0)}d` : "n/a"} />
            <Mini k="R²" v={num(pr.r2, 2)} />
            <Mini k="spread σ" v={pct(pr.std, 1, false)} />
          </div>
        ) : <span className="muted text-[10.5px]">No JP/EN counterpart with overlapping history.</span>}
      </div>
    </Panel>
  );
}

export function PopPanel({ a }: { a: A }) {
  const pc = a.payload.pop_counts ?? {};
  const total = a.pop_total ?? 0;
  const grades = Object.entries(pc).filter(([k]) => /^\d/.test(k)).sort((x, y) => Number(y[0]) - Number(x[0]));
  return (
    <Panel title="Population · PSA">
      <div className="flex gap-2 flex-wrap mb-2">
        <Mini k="total" v={intf(total)} />
        <Mini k="gem rate" v={pct(a.pop_gem_rate, 1, false)} />
        <Mini k="velocity" v={`${num(a.pop_velocity_30d, 1)}/d`} />
        <Mini k="momentum" v={z(a.pop_momentum_z)} t={(a.pop_momentum_z ?? 0) > 1.5 ? "down" : ""} />
      </div>
      {grades.map(([g, n]) => (
        <div key={g} className="flex items-center gap-2 text-[10.5px] py-0.5">
          <span className="w-10 dim">PSA {g}</span>
          <span className="bar flex-1"><span style={{ width: `${total ? (n / total) * 100 : 0}%`, background: g === "10" ? "var(--amber)" : "var(--series-1)" }} /></span>
          <span className="tnum w-14 text-right">{intf(n)}</span>
        </div>
      ))}
    </Panel>
  );
}

export function SealedPanel({ a }: { a: A }) {
  const s = a.payload.sealed_ev;
  const p = a.payload.product;
  if (!s) return null;
  return (
    <Panel title={`Sealed EV · ${p?.type ?? ""} · ${s.n_packs} packs`}>
      <div className="grid grid-cols-2 gap-4">
        <KV rows={[
          ["Market price", money(s.price)],
          ["Expected value (net of fees, liquidity haircut)", money(s.ev)],
          ["EV / price", <b key="r" className={tone(s.ev_ratio - 1)}>{num(s.ev_ratio, 2)}</b>],
          ["Mispricing", <span key="m" className={tone(s.mispricing)}>{money(s.mispricing)} ({pct(s.mispricing_pct)})</span>],
          ["P(open value > price)", pct(s.p_profit, 0, false)],
          ["σ per unit", money(s.std)],
          ["P5 / P50 / P95", `${money(s.percentiles["5"])} / ${money(s.percentiles["50"])} / ${money(s.percentiles["95"])}`],
        ]} />
        <table className="grid-table">
          <thead><tr><th>Slot</th><th className="num">P/pack</th><th className="num">E[hits]</th><th className="num">Avg card</th><th className="num">EV</th></tr></thead>
          <tbody>
            {s.slot_contributions.map((c) => (
              <tr key={c.slot}><td>{c.slot}</td><td className="num tnum">1:{(1 / c.prob_per_pack).toFixed(0)}</td><td className="num tnum">{num(c.expected_hits, 2)}</td>
                <td className="num tnum">{money(c.mean_card_value)}</td><td className="num tnum">{money(c.ev)}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

export function Mini({ k, v, t = "" }: { k: string; v: React.ReactNode; t?: string }) {
  return (
    <span className="inline-block border px-2 py-0.5" style={{ borderColor: "var(--border-strong)" }}>
      <span className="muted text-[9.5px] uppercase tracking-wider mr-1.5">{k}</span><span className={`tnum ${t}`}>{v}</span>
    </span>
  );
}

function Spark({ values }: { values: number[] }) {
  if (!values?.length) return null;
  const w = 220, h = 36;
  const min = Math.min(...values, 0), max = Math.max(...values, 0);
  const sx = (i: number) => (i / Math.max(1, values.length - 1)) * w;
  const sy = (v: number) => h - ((v - min) / (max - min || 1)) * h;
  const zero = sy(0);
  const d = values.map((v, i) => `${i ? "L" : "M"}${sx(i).toFixed(1)},${sy(v).toFixed(1)}`).join(" ");
  return (
    <svg width={w} height={h} className="mt-2 block" role="img" aria-label="Speculative premium, last 60 months">
      <line x1={0} x2={w} y1={zero} y2={zero} stroke="var(--border-strong)" strokeWidth={1} />
      <path d={d} fill="none" stroke="var(--series-2)" strokeWidth={2} />
    </svg>
  );
}
