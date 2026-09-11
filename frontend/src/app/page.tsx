import { api } from "@/lib/api";
import { ScreenerTable } from "@/components/screener-table";
import { TickerTape } from "@/components/ticker-tape";
import type { Screener } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function Home() {
  let initial: Screener = { as_of: "", sort: "screener_score", desc: true, count: 0, rows: [] };
  let err: string | null = null;
  try {
    initial = await api.screener("?limit=500");
  } catch (e) {
    err = (e as Error).message;
  }
  const movers = [...initial.rows].sort((a, b) => Math.abs(b.ret_30d ?? 0) - Math.abs(a.ret_30d ?? 0));
  return (
    <>
      <TickerTape rows={movers} />
      {err && <div className="m-2 p-2 panel down">API unreachable: {err}. Start the backend (make api) and seed it (make seed).</div>}
      <ScreenerTable initial={initial} />
    </>
  );
}
