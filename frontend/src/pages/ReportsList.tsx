import { Link, useSearchParams } from "react-router-dom";

import { type Report, useReports } from "../api/reports";
import { StatusBadge } from "../components/StatusBadge";

const ONGLETS = [
  { key: "", label: "Tous" },
  { key: "dmarc", label: "DMARC" },
  { key: "tls", label: "TLS" },
] as const;

export function ReportsList() {
  const [sp, setSp] = useSearchParams();
  const status = sp.get("status") ?? "";
  const brand = sp.get("brand") ?? "";
  const kind = sp.get("kind") ?? "";
  const page = Number(sp.get("page") ?? 1);

  const set = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    v ? n.set(k, v) : n.delete(k);
    n.set("page", "1");
    setSp(n);
  };

  const { data, isLoading } = useReports({ status, brand, kind, page });

  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold mb-4">Rapports reçus</h1>

      {/* Onglets par type : pilotent ?kind=. « Tous » = pas de parametre. */}
      <div className="flex gap-1 mb-4 border-b">
        {ONGLETS.map((o) => (
          <button
            key={o.key}
            onClick={() => set("kind", o.key)}
            className={`px-3 py-1.5 text-sm -mb-px border-b-2 ${
              kind === o.key
                ? "border-blue-600 text-blue-600 font-medium"
                : "border-transparent text-gray-500 hover:text-gray-800"
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>

      <div className="flex gap-3 mb-4">
        <select value={status} onChange={(e) => set("status", e.target.value)}
                className="border rounded px-2 py-1">
          <option value="">Tous statuts</option>
          <option value="ok">OK</option>
          <option value="partial">Partiel</option>
          <option value="failed">Échec</option>
        </select>
        <input placeholder="Marque / expéditeur" defaultValue={brand}
               onBlur={(e) => set("brand", e.target.value)}
               className="border rounded px-2 py-1" />
      </div>

      {isLoading ? (
        <p>Chargement…</p>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-gray-500 border-b">
            <tr>
              <th className="py-2">Reçu</th>
              <th>Type</th>
              <th>Organisation émettrice</th>
              <th>Source</th>
              <th>Lignes</th>
              <th>Volume · échec</th>
              <th>Statut</th>
            </tr>
          </thead>
          <tbody>
            {data!.items.map((r) => (
              <tr key={r.id} className="border-b hover:bg-gray-50">
                <td className="py-2">
                  <Link to={`/reports/${r.id}`} className="text-blue-600">
                    {new Date(r.created_at).toLocaleString()}
                  </Link>
                </td>
                <td><TypeBadge kind={r.kind} /></td>
                <td>{r.reporter ?? "—"}</td>
                <td>{r.source_type}</td>
                <td>{r.row_count}</td>
                <td>
                  <span className="tabular-nums">{fmtVolume(r)}</span>
                  <span className="ml-2 text-gray-500">{fmtRate(r)}</span>
                </td>
                <td>
                  <StatusBadge status={r.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="flex gap-2 mt-4 items-center">
        <button disabled={page <= 1} onClick={() => set("page", String(page - 1))}
                className="disabled:opacity-40">←</button>
        <span className="text-sm">Page {page} · {data?.total ?? 0} rapports</span>
        <button disabled={(data?.items.length ?? 0) < 50} onClick={() => set("page", String(page + 1))}
                className="disabled:opacity-40">→</button>
      </div>
    </div>
  );
}

function TypeBadge({ kind }: { kind: Report["kind"] }) {
  const tls = kind === "tls";
  return (
    <span className={`rounded px-1.5 py-0.5 text-xs ${
      tls ? "bg-purple-100 text-purple-800" : "bg-blue-100 text-blue-800"
    }`}>
      {tls ? "TLS" : "DMARC"}
    </span>
  );
}

/* Volume : « — » si le total est illisible (null), jamais « 0 ». « au moins N » si le total
   n'est qu'un minorant (un compteur illisible). Meme convention que IpPanel/MtaStsPanel. */
function fmtVolume(r: Report): string {
  if (r.total_units === null) return "—";
  const n = r.total_units.toLocaleString("fr-FR");
  return r.units_partial ? `au moins ${n}` : n;
}

/* Taux d'echec : « — » si le total est inconnu ou nul (pas de « 0 % » rassurant et faux). */
function fmtRate(r: Report): string {
  if (r.total_units === null || r.total_units === 0) return "—";
  const pct = Math.round((100 * (r.failing_units ?? 0)) / r.total_units);
  return `${pct} % en échec`;
}
