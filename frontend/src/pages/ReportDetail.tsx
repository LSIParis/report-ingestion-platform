import { useState } from "react";
import { useParams } from "react-router-dom";

import {
  type ParsingError,
  useReport,
  useReportErrors,
  useReportRows,
  useReprocess,
} from "../api/reports";
import { StatusBadge } from "../components/StatusBadge";

export function ReportDetail() {
  const { id } = useParams<{ id: string }>();
  const [tab, setTab] = useState<"data" | "errors">("data");
  const report = useReport(id!);
  const errors = useReportErrors(id!);
  const reprocess = useReprocess();

  if (report.isLoading) return <p className="p-6">Chargement…</p>;
  const r = report.data!;

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-xl font-semibold">Rapport {r.id.slice(0, 8)}</h1>
          <p className="text-sm text-gray-500">
            {r.source_type} · {r.row_count} lignes · <StatusBadge status={r.status} />
          </p>
        </div>
        <button
          onClick={() => reprocess.mutate(r.id)}
          disabled={reprocess.isPending}
          className="bg-blue-600 text-white rounded px-3 py-1 disabled:opacity-40"
        >
          {reprocess.isPending ? "…" : "Rejouer le parsing"}
        </button>
      </div>

      <div className="flex gap-4 border-b mb-4">
        <button onClick={() => setTab("data")}
                className={tab === "data" ? "border-b-2 border-blue-600 pb-1" : "pb-1"}>
          Données
        </button>
        <button onClick={() => setTab("errors")}
                className={tab === "errors" ? "border-b-2 border-blue-600 pb-1" : "pb-1"}>
          Erreurs ({errors.data?.length ?? 0})
        </button>
      </div>

      {tab === "data" ? <RowsTable reportId={r.id} /> : <ErrorsList errors={errors.data ?? []} />}
    </div>
  );
}

function RowsTable({ reportId }: { reportId: string }) {
  const [page, setPage] = useState(1);
  const { data, isLoading } = useReportRows(reportId, page);
  if (isLoading) return <p>Chargement…</p>;
  const rows = data!.items;
  if (!rows.length) return <p className="text-gray-500">Aucune donnée.</p>;
  const cols = Object.keys(rows[0]);

  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-gray-500 border-b">
            <tr>{cols.map((c) => <th key={c} className="py-2 pr-4">{c}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="border-b">
                {cols.map((c) => <td key={c} className="py-1 pr-4">{String(row[c] ?? "—")}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex gap-2 mt-4 items-center">
        <button disabled={page <= 1} onClick={() => setPage(page - 1)} className="disabled:opacity-40">←</button>
        <span className="text-sm">Page {page} · {data?.total} lignes</span>
        <button disabled={rows.length < 50} onClick={() => setPage(page + 1)} className="disabled:opacity-40">→</button>
      </div>
    </>
  );
}

function ErrorsList({ errors }: { errors: ParsingError[] }) {
  if (!errors.length) return <p className="text-gray-500">Aucune erreur.</p>;
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-gray-500 border-b">
        <tr>
          <th className="py-2">Sévérité</th>
          <th>Code</th>
          <th>Champ</th>
          <th>Ligne</th>
          <th>Message</th>
        </tr>
      </thead>
      <tbody>
        {errors.map((e) => (
          <tr key={e.id} className="border-b">
            <td className="py-1">{e.severity}</td>
            <td>{e.code}</td>
            <td>{e.context?.field ?? "—"}</td>
            <td>{e.context?.row_index ?? "—"}</td>
            <td>{e.message}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
