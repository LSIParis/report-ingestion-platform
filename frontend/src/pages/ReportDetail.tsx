import { useState } from "react";
import { useParams } from "react-router-dom";

import {
  type ParsingError,
  useReport,
  useReportErrors,
  useReportRows,
  useReprocess,
} from "../api/reports";
import { IpPanel } from "../components/IpPanel";
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
  const [ip, setIp] = useState<string | null>(null);
  const { data, isLoading } = useReportRows(reportId, page);
  if (isLoading) return <p>Chargement…</p>;
  const rows = data!.items;
  if (!rows.length) return <p className="text-gray-500">Aucune donnée.</p>;

  // Une ligne DMARC se reconnaît à ses DONNÉES, pas à un nom de profil : `Report` ne
  // stocke pas le format, seulement source_type (attachment/body) et profile_id.
  const isDmarc = "source_ip" in rows[0];

  return (
    <>
      {isDmarc ? <DmarcTable rows={rows} onSelectIp={setIp} /> : <GenericTable rows={rows} />}
      <div className="flex gap-2 mt-4 items-center">
        <button disabled={page <= 1} onClick={() => setPage(page - 1)} className="disabled:opacity-40">←</button>
        <span className="text-sm">Page {page} · {data?.total} lignes</span>
        <button disabled={rows.length < 50} onClick={() => setPage(page + 1)} className="disabled:opacity-40">→</button>
      </div>
      {ip && <IpPanel ip={ip} onClose={() => setIp(null)} />}
    </>
  );
}

/** Les lignes DMARC méritent mieux qu'un vidage de JSON : ce sont elles qu'on lit pour
 *  décider. L'IP est le seul point d'entrée de l'enquête — donc elle est cliquable. */
function DmarcTable({
  rows,
  onSelectIp,
}: {
  rows: Record<string, unknown>[];
  onSelectIp: (ip: string) => void;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-gray-500 border-b">
          <tr>
            <th className="py-2 pr-4">IP source</th>
            <th className="py-2 pr-4">Messages</th>
            <th className="py-2 pr-4">Alignement</th>
            <th className="py-2 pr-4">Traitement</th>
            <th className="py-2 pr-4">SPF / DKIM</th>
            <th className="py-2 pr-4">De</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const aligned = String(row.aligned ?? "");
            return (
              <tr key={i} className="border-b">
                <td className="py-1 pr-4">
                  <button
                    onClick={() => onSelectIp(String(row.source_ip))}
                    className="font-mono text-blue-600 hover:underline"
                  >
                    {String(row.source_ip)}
                  </button>
                </td>
                <td className="py-1 pr-4">{String(row.message_count ?? "—")}</td>
                <td className="py-1 pr-4">
                  <span className={aligned === "pass" ? "text-green-700" : "text-red-700"}>
                    {aligned || "—"}
                  </span>
                </td>
                <td className="py-1 pr-4">{String(row.disposition ?? "—")}</td>
                <td className="py-1 pr-4 text-gray-500">
                  {String(row.spf ?? "—")} / {String(row.dkim ?? "—")}
                </td>
                <td className="py-1 pr-4">{String(row.header_from ?? "—")}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Les autres rapports (CSV, XLSX, PDF…) gardent le rendu générique : on ne connaît pas
 *  leurs colonnes à l'avance. */
function GenericTable({ rows }: { rows: Record<string, unknown>[] }) {
  const cols = Object.keys(rows[0]);
  return (
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
