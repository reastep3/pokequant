import Link from "next/link";
import { api } from "@/lib/api";
import { money, num, pct, tone } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function SealedPage() {
  const data = await api.sealed().catch(() => ({ count: 0, rows: [] }));
  return (
    <div className="panel m-2">
      <div className="panel-h"><span>Sealed EV matrix · box price vs Σ packs × pull rate × singles value</span><span className="normal-case tracking-normal dim">{data.count} SKUs · net of {`13.25%`} fees + 15% liquidity haircut</span></div>
      <div className="overflow-auto">
        <table className="grid-table">
          <thead><tr>
            <th>Ticker</th><th>Product</th><th>L</th><th className="num">Packs</th><th className="num">Price</th><th className="num">EV</th><th className="num">EV/Px</th>
            <th className="num">Mispricing</th><th className="num">P(profit)</th><th className="num">P5</th><th className="num">P95</th><th className="num">DD90</th><th className="num">Liq</th><th className="num">K-ROI</th>
          </tr></thead>
          <tbody>
            {data.rows.map((r) => (
              <tr key={r.id}>
                <td><Link href={`/asset/${r.id}`} className="amber">{r.ticker}</Link></td>
                <td className="dim">{r.name}</td>
                <td><span className="tag">{r.language.toUpperCase()}</span></td>
                <td className="num tnum">{r.product?.packs ?? "—"}</td>
                <td className="num tnum"><b>{money(r.market_price)}</b></td>
                <td className="num tnum">{money(r.sealed_ev)}</td>
                <td className={`num tnum ${tone((r.sealed_ev_ratio ?? 1) - 1)}`}><b>{num(r.sealed_ev_ratio, 2)}</b></td>
                <td className={`num tnum ${tone(r.ev_gap_pct)}`}>{pct(r.ev_gap_pct)}</td>
                <td className="num tnum">{pct(r.sealed_p_profit, 0, false)}</td>
                <td className="num tnum dim">{money(r.ev?.percentiles?.["5"])}</td>
                <td className="num tnum dim">{money(r.ev?.percentiles?.["95"])}</td>
                <td className="num tnum down">{pct(r.dd_90d, 0, false)}</td>
                <td className="num tnum">{num(r.liquidity_score, 0)}</td>
                <td className={`num tnum ${tone(r.kelly_adj_roi)}`}>{pct(r.kelly_adj_roi, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="p-2 text-[10.5px] muted">EV = Σ<sub>slots</sub> packs × P(hit) × E[card value] × (1 − fees)(1 − haircut) + bulk. Distribution by Monte Carlo over Binomial(packs, p) hits. Ranked by mispricing.</div>
    </div>
  );
}
