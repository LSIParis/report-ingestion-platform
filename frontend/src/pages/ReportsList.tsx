import { Link, useSearchParams } from "react-router-dom";

import { useReports } from "../api/reports";
import { StatusBadge } from "../components/StatusBadge";

export function ReportsList() {
  const [sp, setSp] = useSearchParams();
  const status = sp.get("status") ?? "";
  const brand = sp.get("brand") ?? "";
  const page = Number(sp.get("page") ?? 1);

  const set = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    v ? n.set(k, v) : n.delete(k);
    n.set("page", "1");
    setSp(n);
  };

  const { data, isLoading } = useReports({ status, brand, page });

  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold mb-4">Rapports reçus</h1>
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
              <th>Source</th>
              <th>Lignes</th>
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
                <td>{r.source_type}</td>
                <td>{r.row_count}</td>
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
