import Link from "next/link";
import type { ScreenerRow } from "@/lib/types";
import { money, pct, tone } from "@/lib/format";

export function TickerTape({ rows }: { rows: ScreenerRow[] }) {
  const items = rows.filter((r) => r.market_price != null).slice(0, 40);
  const seq = [...items, ...items];
  return (
    <div className="tape">
      <div className="tape-inner">
        {seq.map((r, i) => (
          <Link key={`${r.id}-${i}`} href={`/asset/${r.id}`} className="inline-block px-3 text-[11px]">
            <span className="dim">{r.ticker}</span> <span className="tnum">{money(r.market_price)}</span>{" "}
            <span className={`tnum ${tone(r.ret_30d)}`}>{pct(r.ret_30d)}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}
