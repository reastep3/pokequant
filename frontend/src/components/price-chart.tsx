"use client";

import { useEffect, useRef, useState } from "react";
import { ColorType, CrosshairMode, LineSeries, LineStyle, createChart, type ISeriesApi, type LineData, type Time } from "lightweight-charts";
import type { Bar } from "@/lib/types";
import { money } from "@/lib/format";

export type FairCurve = { time: string; fair: number; lo: number; hi: number }[];

const MONO = 'ui-monospace, "SF Mono", Menlo, Consolas, monospace';

export function PriceChart({ bars, fair, fundamental }: { bars: Bar[]; fair: FairCurve; fundamental?: number | null }) {
  const ref = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<{ x: number; y: number; t: string; px?: number; fv?: number } | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el || bars.length === 0) return;
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#0b0d10" }, textColor: "#9aa5b4", fontFamily: MONO, fontSize: 10 },
      grid: { vertLines: { color: "#141920" }, horzLines: { color: "#141920" } },
      rightPriceScale: { borderColor: "#1f2730" },
      timeScale: { borderColor: "#1f2730", rightOffset: 4 },
      crosshair: { mode: CrosshairMode.Normal, vertLine: { color: "#2c3742", labelBackgroundColor: "#182029" }, horzLine: { color: "#2c3742", labelBackgroundColor: "#182029" } },
      handleScroll: { mouseWheel: false },
    });
    const market = chart.addSeries(LineSeries, { color: "#3987e5", lineWidth: 2, priceLineVisible: false, lastValueVisible: true, title: "" });
    market.setData(bars.map((b) => ({ time: b.time as Time, value: b.value })));
    let fairS: ISeriesApi<"Line"> | null = null;
    if (fair.length > 1) {
      fairS = chart.addSeries(LineSeries, { color: "#d95926", lineWidth: 2, priceLineVisible: false, lastValueVisible: true });
      fairS.setData(fair.map((f) => ({ time: f.time as Time, value: f.fair })));
      for (const key of ["lo", "hi"] as const) {
        const s = chart.addSeries(LineSeries, { color: "rgba(217,89,38,0.45)", lineWidth: 1, lineStyle: LineStyle.Dotted, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
        s.setData(fair.map((f) => ({ time: f.time as Time, value: f[key] })));
      }
    }
    if (fundamental && fundamental > 0) {
      market.createPriceLine({ price: fundamental, color: "#199e70", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "CS fund." });
    }
    chart.timeScale().fitContent();
    chart.subscribeCrosshairMove((p) => {
      if (!p.point || !p.time) { setTip(null); return; }
      const m = p.seriesData.get(market) as LineData | undefined;
      const f = fairS ? (p.seriesData.get(fairS) as LineData | undefined) : undefined;
      setTip({ x: p.point.x, y: p.point.y, t: String(p.time), px: m?.value, fv: f?.value });
    });
    return () => chart.remove();
  }, [bars, fair, fundamental]);

  return (
    <div className="relative">
      <div className="chart-legend">
        <span><i style={{ background: "#3987e5" }} />Market (daily)</span>
        {fair.length > 1 && <span><i style={{ background: "#d95926" }} />Kernel fair value · dotted = 95% band</span>}
        {fundamental ? <span><i style={{ background: "#199e70" }} />Campbell–Shiller fundamental</span> : null}
      </div>
      <div ref={ref} style={{ height: 320 }} />
      {tip && (
        <div className="chart-tip" style={{ left: Math.min(tip.x + 12, 600), top: tip.y + 20 }}>
          <div className="dim">{tip.t}</div>
          {tip.px !== undefined && <div>Market <b className="tnum">{money(tip.px)}</b></div>}
          {tip.fv !== undefined && <div>Fair <b className="tnum">{money(tip.fv)}</b></div>}
        </div>
      )}
    </div>
  );
}
