"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { PUBLIC_API, fetcher } from "@/lib/api";
import { clock } from "@/lib/format";

const NAV = [
  { href: "/", label: "SCREENER", key: "F1" },
  { href: "/sealed", label: "SEALED EV", key: "F2" },
  { href: "/signals", label: "SIGNALS", key: "F3" },
];

export function TerminalShell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const [now, setNow] = useState("");
  const { data: ready } = useSWR<{ status: string; demo_mode: boolean; cache: string }>(`${PUBLIC_API}/ready`, fetcher, { refreshInterval: 30000 });
  useEffect(() => {
    setNow(clock());
    const t = setInterval(() => setNow(clock()), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="min-h-screen flex flex-col">
      <header className="flex items-center gap-4 px-3 h-8 border-b" style={{ borderColor: "var(--border)", background: "#08090c" }}>
        <Link href="/" className="amber font-semibold tracking-widest">POKÉQUANT</Link>
        <nav className="flex gap-1">
          {NAV.map((n) => {
            const active = n.href === "/" ? path === "/" || path.startsWith("/asset") : path.startsWith(n.href);
            return (
              <Link key={n.href} href={n.href} className="px-2 py-0.5 text-[11px] tracking-wider"
                style={{ color: active ? "var(--amber)" : "var(--text-2)", borderBottom: active ? "2px solid var(--amber)" : "2px solid transparent" }}>
                <span className="kbd mr-1">{n.key}</span>{n.label}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-4 text-[11px] dim">
          <span><span className={`live-dot ${ready?.status === "ready" ? "on" : ""}`} /> {ready ? `API ${ready.cache.toUpperCase()}${ready.demo_mode ? " · DEMO DATA" : ""}` : "API …"}</span>
          <span className="tnum">{now}</span>
        </div>
      </header>
      <main className="flex-1">{children}</main>
      <footer className="h-6 px-3 flex items-center text-[10px] muted border-t" style={{ borderColor: "var(--border)" }}>
        Kelly sizing is half-Kelly capped at 25% of bankroll · fair value = robust local-linear kernel regression on log sales · not investment advice
      </footer>
    </div>
  );
}
