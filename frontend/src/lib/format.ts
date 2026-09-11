const usd0 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const usd2 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const int = new Intl.NumberFormat("en-US");

export const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
export const money = (v: number | null | undefined, cents = false) => (isNum(v) ? (cents || Math.abs(v) < 100 ? usd2 : usd0).format(v) : "—");
export const pct = (v: number | null | undefined, d = 1, sign = true) =>
  isNum(v) ? `${sign && v > 0 ? "+" : ""}${(v * 100).toFixed(d)}%` : "—";
export const num = (v: number | null | undefined, d = 2) => (isNum(v) ? v.toFixed(d) : "—");
export const intf = (v: number | null | undefined) => (isNum(v) ? int.format(Math.round(v)) : "—");
export const z = (v: number | null | undefined) => (isNum(v) ? `${v > 0 ? "+" : ""}${v.toFixed(1)}σ` : "—");
export const days = (v: number | null | undefined) => (v === null || v === undefined ? "open" : `${v}d`);
export const tone = (v: number | null | undefined, invert = false) => (!isNum(v) || v === 0 ? "" : (v > 0) !== invert ? "up" : "down");
export const clock = (d = new Date()) => d.toISOString().slice(11, 19) + "Z";
export const short = (iso: string | null | undefined) => (iso ? iso.slice(0, 16).replace("T", " ") : "—");
