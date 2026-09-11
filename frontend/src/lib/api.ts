import type { AssetDetail, Bar, Screener, SealedRow, Signal } from "./types";

/** Server components reach the API over Fly's private network; the browser uses the public URL. */
export const PUBLIC_API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const INTERNAL_API = process.env.API_INTERNAL_URL ?? PUBLIC_API;

export function apiBase(): string {
  return typeof window === "undefined" ? INTERNAL_API : PUBLIC_API;
}

async function get<T>(path: string, revalidate = 15): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, { next: { revalidate }, headers: { accept: "application/json" } });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json() as Promise<T>;
}

export const fetcher = <T,>(url: string) => fetch(url).then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.json() as Promise<T>; });

export const api = {
  screener: (q = "") => get<Screener>(`/api/v1/screener${q}`),
  asset: (id: string | number) => get<AssetDetail>(`/api/v1/assets/${id}`, 30),
  prices: (id: string | number, grade: string, days = 540) => get<{ bars: Bar[] }>(`/api/v1/assets/${id}/prices?grade=${grade}&days=${days}`, 60),
  sealed: () => get<{ count: number; rows: SealedRow[] }>("/api/v1/sealed/ev"),
  signals: () => get<{ count: number; rows: Signal[] }>("/api/v1/signals"),
  ready: () => get<{ status: string; demo_mode: boolean; cache: string }>("/ready", 5),
};
