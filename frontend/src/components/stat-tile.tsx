export function StatTile({ k, v, s, tone }: { k: string; v: React.ReactNode; s?: React.ReactNode; tone?: string }) {
  return (
    <div className="tile">
      <div className="k">{k}</div>
      <div className={`v tnum ${tone ?? ""}`}>{v}</div>
      {s !== undefined && <div className="s">{s}</div>}
    </div>
  );
}

export function Panel({ title, right, children, className = "" }: { title: string; right?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={`panel ${className}`}>
      <div className="panel-h"><span>{title}</span>{right}</div>
      <div className="panel-b">{children}</div>
    </section>
  );
}

export function KV({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <table className="w-full">
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}><td className="dim py-0.5 pr-3 text-[11px]">{k}</td><td className="tnum text-right py-0.5">{v}</td></tr>
        ))}
      </tbody>
    </table>
  );
}
