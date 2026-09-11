"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { PUBLIC_API, fetcher } from "@/lib/api";
import type { Screener, ScreenerRow } from "@/lib/types";
import { days, intf, isNum, money, num, pct, tone, z } from "@/lib/format";

type Col = { key: keyof ScreenerRow; label: string; fmt: (r: ScreenerRow) => React.ReactNode; num?: boolean; title?: string };

const COLS: Col[] = [
  { key: "ticker", label: "Ticker", fmt: (r) => <Link href={`/asset/${r.id}`} className="amber">{r.ticker}</Link> },
  { key: "name", label: "Name", fmt: (r) => <span className="dim">{r.name}</span> },
  { key: "language", label: "L", fmt: (r) => <span className="tag">{r.language.toUpperCase()}</span> },
  { key: "grade_key", label: "Grade", fmt: (r) => r.grade_key },
  { key: "market_price", label: "Px", num: true, fmt: (r) => <b className="tnum">{money(r.market_price)}</b> },
  { key: "fair_value", label: "Fair", num: true, fmt: (r) => <span className="tnum">{money(r.fair_value)}</span>, title: "Kernel-regression fair value" },
  { key: "ev_gap_pct", label: "EV gap", num: true, fmt: (r) => <span className={`tnum ${tone(r.ev_gap_pct)}`}>{pct(r.ev_gap_pct)}</span>, title: "(fair − market) / market" },
  { key: "kelly_fraction_scaled", label: "Kelly f", num: true, fmt: (r) => <span className="tnum">{pct(r.kelly_fraction_scaled, 1, false)}</span>, title: "Half-Kelly fraction of bankroll, capped" },
  { key: "kelly_adj_roi", label: "K-ROI", num: true, fmt: (r) => <span className={`tnum ${tone(r.kelly_adj_roi)}`}>{pct(r.kelly_adj_roi, 2)}</span>, title: "Kelly fraction × expected 90d return" },
  { key: "liquidity_score", label: "Liq", num: true, fmt: (r) => <LiqBar v={r.liquidity_score} /> },
  { key: "dd_30d", label: "DD30", num: true, fmt: (r) => <span className="tnum down">{pct(r.dd_30d, 0, false)}</span> },
  { key: "dd_60d", label: "DD60", num: true, fmt: (r) => <span className="tnum down">{pct(r.dd_60d, 0, false)}</span> },
  { key: "dd_90d", label: "DD90", num: true, fmt: (r) => <span className="tnum down">{pct(r.dd_90d, 0, false)}</span> },
  { key: "recovery_90d_days", label: "Rec90", num: true, fmt: (r) => <span className="tnum dim">{r.dd_90d && r.dd_90d < 0 ? days(r.recovery_90d_days) : "—"}</span>, title: "Days from trough back to prior peak (open = unrecovered)" },
  { key: "arb_spread_z", label: "Arb z", num: true, fmt: (r) => <span className={`tnum ${Math.abs(r.arb_spread_z ?? 0) >= 2 ? "amber" : ""}`}>{z(r.arb_spread_z)}</span>, title: "Grading spread z-score vs 90d history" },
  { key: "pairs_z", label: "JP/EN z", num: true, fmt: (r) => <span className={`tnum ${Math.abs(r.pairs_z ?? 0) >= 2 ? "amber" : ""}`}>{z(r.pairs_z)}</span> },
  { key: "cs_bubble_premium_pct", label: "Bubble", num: true, fmt: (r) => <span className={`tnum ${tone(r.cs_bubble_premium_pct, true)}`}>{pct(r.cs_bubble_premium_pct, 0)}</span>, title: "Campbell–Shiller speculative premium vs fundamental" },
  { key: "pop_total", label: "Pop", num: true, fmt: (r) => <span className="tnum dim">{intf(r.pop_total)}</span> },
  { key: "pop_velocity_30d", label: "Vel/d", num: true, fmt: (r) => <span className="tnum dim">{num(r.pop_velocity_30d, 1)}</span>, title: "New slabs per day, trailing 30d" },
  { key: "screener_score", label: "Score", num: true, fmt: (r) => <b className="tnum amber">{num(r.screener_score, 3)}</b> },
];

function LiqBar({ v }: { v: number | null }) {
  if (!isNum(v)) return <span>—</span>;
  return (
    <span className="inline-flex items-center gap-1">
      <span className="bar w-10"><span style={{ width: `${v}%`, background: v > 60 ? "var(--up)" : v > 30 ? "var(--warn)" : "var(--down)" }} /></span>
      <span className="tnum">{v.toFixed(0)}</span>
    </span>
  );
}

export function ScreenerTable({ initial }: { initial: Screener }) {
  const url = `${PUBLIC_API}/api/v1/screener?limit=500`;
  const { data, mutate } = useSWR<Screener>(url, fetcher, { fallbackData: initial, refreshInterval: 30000 });
  const [sort, setSort] = useState<keyof ScreenerRow>("screener_score");
  const [desc, setDesc] = useState(true);
  const [type, setType] = useState<"all" | "card" | "sealed">("all");
  const [lang, setLang] = useState<"all" | "en" | "ja">("all");
  const [q, setQ] = useState("");
  const [live, setLive] = useState(false);

  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    const es = new EventSource(`${PUBLIC_API}/api/v1/stream/screener`);
    es.onopen = () => setLive(true);
    es.onerror = () => setLive(false);
    es.addEventListener("screener", (e) => { try { mutate(JSON.parse((e as MessageEvent).data), { revalidate: false }); } catch { /* ignore */ } });
    return () => es.close();
  }, [mutate]);

  const rows = useMemo(() => {
    const list = (data?.rows ?? []).filter((r) => (type === "all" || r.asset_type === type) && (lang === "all" || r.language === lang)
      && (!q || `${r.ticker} ${r.name} ${r.set_name ?? ""}`.toLowerCase().includes(q.toLowerCase())));
    return [...list].sort((a, b) => {
      const av = a[sort], bv = b[sort];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      const c = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv));
      return desc ? -c : c;
    });
  }, [data, sort, desc, type, lang, q]);

  const onSort = (k: keyof ScreenerRow) => { if (k === sort) setDesc(!desc); else { setSort(k); setDesc(true); } };

  return (
    <div className="panel m-2">
      <div className="panel-h">
        <span>Market screener · ranked by Kelly-adjusted ROI × liquidity</span>
        <div className="flex items-center gap-3 normal-case tracking-normal" style={{ color: "var(--text-2)" }}>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="filter…" className="bg-transparent border px-1 w-32 text-[11px]" style={{ borderColor: "var(--border-strong)", color: "var(--text)" }} />
          <Seg value={type} onChange={setType} opts={["all", "card", "sealed"]} />
          <Seg value={lang} onChange={setLang} opts={["all", "en", "ja"]} />
          <span><span className={`live-dot ${live ? "on" : ""}`} /> {live ? "LIVE" : "POLL 30s"}</span>
          <span className="tnum">{rows.length} rows · {data?.as_of?.slice(11, 19)}Z</span>
        </div>
      </div>
      <div className="overflow-auto" style={{ maxHeight: "calc(100vh - 140px)" }}>
        <table className="grid-table">
          <thead>
            <tr>{COLS.map((c) => (
              <th key={c.key} onClick={() => onSort(c.key)} className={`${c.num ? "num" : ""} ${sort === c.key ? "active" : ""}`} title={c.title}>
                {c.label}{sort === c.key ? (desc ? " ▼" : " ▲") : ""}
              </th>))}</tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.id}-${r.grade_key}`}>{COLS.map((c) => <td key={c.key} className={c.num ? "num" : ""}>{c.fmt(r)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Seg<T extends string>({ value, onChange, opts }: { value: T; onChange: (v: T) => void; opts: T[] }) {
  return (
    <span className="inline-flex border" style={{ borderColor: "var(--border-strong)" }}>
      {opts.map((o) => (
        <button key={o} onClick={() => onChange(o)} className="px-1.5 text-[10px] uppercase"
          style={{ background: value === o ? "var(--amber-dim)" : "transparent", color: value === o ? "var(--text)" : "var(--text-2)" }}>{o}</button>
      ))}
    </span>
  );
}
