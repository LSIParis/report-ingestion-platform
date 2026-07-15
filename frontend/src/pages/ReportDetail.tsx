import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  type ParsingError,
  type Report,
  type ReportBreakdown,
  useReport,
  useReportBreakdown,
  useReportErrors,
  useReportRows,
  useReprocess,
} from "../api/reports";
import { isAdmin } from "../auth/session";
import { useTenant } from "../auth/tenant";
import { IpPanel } from "../components/IpPanel";
import { MtaStsPanel } from "../components/MtaStsPanel";
import { StatusBadge } from "../components/StatusBadge";

export function ReportDetail() {
  const { id } = useParams<{ id: string }>();
  const [tab, setTab] = useState<"data" | "errors">("data");
  const report = useReport(id!);
  const breakdown = useReportBreakdown(id!);
  const errors = useReportErrors(id!);
  const reprocess = useReprocess();

  if (report.isLoading) return <p className="p-6">Chargement…</p>;
  const r = report.data!;

  return (
    <div className="p-6">
      <Synthese r={r} breakdown={breakdown.data} />

      <div className="mb-4 flex justify-end">
        <button
          onClick={() => reprocess.mutate(r.id)}
          disabled={reprocess.isPending}
          className="rounded bg-blue-600 px-3 py-1 text-white disabled:opacity-40"
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

      {tab === "data"
        ? <DataView r={r} breakdown={breakdown.data} loading={breakdown.isLoading} />
        : <ErrorsList errors={errors.data ?? []} />}
    </div>
  );
}

/* Bandeau de synthese : l'essentiel du rapport sans lire les lignes. Kind-aware. */
function Synthese({ r, breakdown }: { r: Report; breakdown?: ReportBreakdown }) {
  const { tenant } = useTenant();
  const [mtaSts, setMtaSts] = useState(false);
  const domain = breakdown?.policy_domain ?? null;
  // Le lien domaine -> MtaStsPanel exige un composant admin ET un tenant concret.
  const domaineCliquable = domain != null && isAdmin() && tenant != null;

  return (
    <div className="mb-4 rounded border bg-white p-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <TypeBadge kind={r.kind} />
        <h1 className="text-lg font-semibold">{r.id.slice(0, 8)}</h1>
        <StatusBadge status={r.status} />
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
        <Fait label="Émetteur">
          <Link to={`/reports?reporter=${encodeURIComponent(r.reporter ?? "")}`}
                className="text-blue-600 hover:underline">
            {r.reporter ?? "—"}
          </Link>
        </Fait>
        <Fait label="Domaine">
          {domaineCliquable ? (
            <button onClick={() => setMtaSts(true)} className="text-blue-600 hover:underline">
              {domain}
            </button>
          ) : (domain ?? "—")}
        </Fait>
        <Fait label="Période">
          {r.period_start ?? "?"} → {r.period_end ?? "?"}
        </Fait>
        <Fait label="Volume">{fmtVolume(r)}</Fait>
        <Fait label="Taux d'échec">{fmtRate(r)}</Fait>
      </dl>

      {r.kind === "dmarc" && breakdown && r.total_units !== null && (
        <div className="mt-3 space-y-2">
          <Barre label="DKIM aligné" value={breakdown.dkim_aligned ?? 0} total={r.total_units} />
          <Barre label="SPF aligné" value={breakdown.spf_aligned ?? 0} total={r.total_units} />
        </div>
      )}

      {r.kind === "tls" && <VerdictTls r={r} />}

      {mtaSts && tenant && domain && (
        <MtaStsPanel tenantId={tenant} domain={domain} onClose={() => setMtaSts(false)} />
      )}
    </div>
  );
}

/* Verdict TLS derive des champs du cycle 1 : sur pour enforce = aucun echec ET total
   entierement lisible. On ne dit jamais "sur" sur une magnitude partielle/inconnue. */
function VerdictTls({ r }: { r: Report }) {
  const sur = r.total_units !== null && !r.units_partial && r.failing_units === 0;
  return (
    <div className={`mt-3 rounded border p-3 text-sm ${
      sur ? "border-green-200 bg-green-50 text-green-900"
          : "border-red-200 bg-red-50 text-red-900"}`}>
      {sur
        ? "Chiffrement vérifié : sûr de passer en application (enforce)."
        : "Des sessions échouent ou sont illisibles — à corriger avant d'appliquer."}
    </div>
  );
}

function DataView({ r, breakdown, loading }:
    { r: Report; breakdown?: ReportBreakdown; loading: boolean }) {
  // DMARC : vue groupee par IP (breakdown). TLS/generique : rendu ligne a ligne existant.
  // Chaque branche gere son propre etat `ip`/`IpPanel` (branches mutuellement exclusives).
  if (r.kind === "dmarc") {
    return <DmarcSources sources={breakdown?.sources ?? []} loading={loading} />;
  }
  return <RowsLegacy reportId={r.id} />;
}

function DmarcSources({ sources, loading }:
    { sources: NonNullable<ReportBreakdown["sources"]>; loading: boolean }) {
  const [ip, setIp] = useState<string | null>(null);
  if (loading) return <p>Chargement…</p>;
  if (!sources.length) return <p className="text-gray-500">Aucune source.</p>;
  return (
    <>
      <SourcesTable sources={sources} onSelectIp={setIp} />
      {ip && <IpPanel ip={ip} onClose={() => setIp(null)} />}
    </>
  );
}

/* Vue groupee par IP (DMARC) : une ligne par IP source, coherente avec le tableau
   Sources de la Vue d'ensemble. L'IP est le point d'entree de l'enquete -> cliquable. */
function SourcesTable({ sources, onSelectIp }:
    { sources: NonNullable<ReportBreakdown["sources"]>; onSelectIp: (ip: string) => void }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="border-b text-left text-gray-500">
          <tr>
            <th className="py-2 pr-4">IP source</th>
            <th className="py-2 pr-4 text-right">Messages</th>
            <th className="py-2 pr-4 text-right">Conformes</th>
            <th className="py-2 pr-4 text-right">En échec</th>
          </tr>
        </thead>
        <tbody>
          {sources.map((s) => (
            <tr key={s.source_ip} className="border-b">
              <td className="py-1 pr-4">
                <button onClick={() => onSelectIp(s.source_ip)}
                        className="font-mono text-blue-600 hover:underline">
                  {s.source_ip}
                </button>
              </td>
              <td className="py-1 pr-4 text-right tabular-nums">{s.messages}</td>
              <td className="py-1 pr-4 text-right tabular-nums text-green-700">{s.compliant}</td>
              <td className="py-1 pr-4 text-right tabular-nums text-red-700">
                {s.failing || "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* TLS et generique : rendu ligne a ligne (inchange). La branche DMARC de l'ancien
   RowsTable a disparu -- DMARC passe par DmarcSources. */
function RowsLegacy({ reportId }: { reportId: string }) {
  const [page, setPage] = useState(1);
  const [ip, setIp] = useState<string | null>(null);
  const { data, isLoading } = useReportRows(reportId, page);
  if (isLoading) return <p>Chargement…</p>;
  const items = data!.items;
  if (!items.length) return <p className="text-gray-500">Aucune donnée.</p>;
  const rows = items.map((r) => r.data);
  const isTls = "kind" in rows[0] && "policy_domain" in rows[0];
  return (
    <>
      {isTls ? <TlsTable rows={rows} onSelectIp={setIp} /> : <GenericTable rows={rows} />}
      <div className="flex gap-2 mt-4 items-center">
        <button disabled={page <= 1} onClick={() => setPage(page - 1)} className="disabled:opacity-40">←</button>
        <span className="text-sm">Page {page} · {data?.total} lignes</span>
        <button disabled={items.length < 50} onClick={() => setPage(page + 1)} className="disabled:opacity-40">→</button>
      </div>
      {ip && <IpPanel ip={ip} onClose={() => setIp(null)} />}
    </>
  );
}

/** Un rapport TLS mêle deux natures de lignes : le bilan chiffré d'une politique, et le
 *  détail de chaque échec. Les afficher pêle-mêle dans une table à colonnes fixes
 *  produirait une forêt de tirets. On les sépare. */
function TlsTable({
  rows,
  onSelectIp,
}: {
  rows: Record<string, unknown>[];
  onSelectIp: (ip: string) => void;
}) {
  const summaries = rows.filter((r) => r.kind === "summary");
  const failures = rows.filter((r) => r.kind === "failure");

  return (
    <div className="space-y-6">
      {summaries.length > 0 && (
        <div>
          <h3 className="mb-2 text-xs uppercase tracking-wide text-gray-400">Sessions</h3>
          <table className="w-full text-sm">
            <thead className="border-b text-left text-gray-500">
              <tr>
                <th className="py-2 pr-4">Politique</th>
                <th className="py-2 pr-4">Serveurs couverts</th>
                <th className="py-2 pr-4">Chiffrées</th>
                <th className="py-2 pr-4">En échec</th>
              </tr>
            </thead>
            <tbody>
              {summaries.map((r, i) => (
                <tr key={i} className="border-b">
                  <td className="py-1 pr-4">{String(r.policy_type ?? "—")}</td>
                  <td className="py-1 pr-4 font-mono text-xs">{String(r.mx_host ?? "—")}</td>
                  <td className="py-1 pr-4 text-green-700">
                    {String(r.successful_sessions ?? "—")}
                  </td>
                  <td className="py-1 pr-4 text-red-700">
                    {String(r.failed_sessions ?? "—")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {failures.length > 0 && (
        <div>
          <h3 className="mb-2 text-xs uppercase tracking-wide text-gray-400">
            Échecs de chiffrement
          </h3>
          <table className="w-full text-sm">
            <thead className="border-b text-left text-gray-500">
              <tr>
                <th className="py-2 pr-4">Type d'échec</th>
                <th className="py-2 pr-4">Sessions</th>
                <th className="py-2 pr-4">MTA émetteur</th>
                <th className="py-2 pr-4">Serveur visé</th>
              </tr>
            </thead>
            <tbody>
              {failures.map((r, i) => (
                <tr key={i} className="border-b">
                  <td className="py-1 pr-4">{String(r.result_type ?? "—")}</td>
                  <td className="py-1 pr-4">{String(r.failure_sessions ?? "—")}</td>
                  <td className="py-1 pr-4">
                    {r.sending_mta_ip ? (
                      // Une IP qui échoue en TLS mérite la même enquête qu'une IP rejetée
                      // en DMARC : c'est le même panneau.
                      <button
                        onClick={() => onSelectIp(String(r.sending_mta_ip))}
                        className="font-mono text-blue-600 hover:underline"
                      >
                        {String(r.sending_mta_ip)}
                      </button>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="py-1 pr-4 font-mono text-xs">
                    {String(r.receiving_mx_hostname ?? "—")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
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

function TypeBadge({ kind }: { kind: Report["kind"] }) {
  const tls = kind === "tls";
  return (
    <span className={`rounded px-1.5 py-0.5 text-xs ${
      tls ? "bg-purple-100 text-purple-800" : "bg-blue-100 text-blue-800"}`}>
      {tls ? "TLS" : "DMARC"}
    </span>
  );
}

function Fait({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2">
      <dt className="text-gray-500">{label}</dt>
      <dd className="min-w-0 break-words font-medium">{children}</dd>
    </div>
  );
}

function Barre({ label, value, total }: { label: string; value: number; total: number }) {
  const pct = total ? Math.round((100 * value) / total) : 0;
  return (
    <div>
      <div className="flex justify-between text-xs text-gray-600">
        <span>{label}</span>
        <span className="tabular-nums">{total ? `${pct} %` : "—"}</span>
      </div>
      <div className="mt-1 h-1.5 rounded bg-gray-100">
        <div className="h-1.5 rounded bg-emerald-500" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/* Convention du cycle 1 : « — » si le total est illisible (jamais « 0 »/« 0 % ») ;
   « au moins N » si le total n'est qu'un minorant. */
function fmtVolume(r: Report): string {
  if (r.total_units === null) return "—";
  const n = r.total_units.toLocaleString("fr-FR");
  return r.units_partial ? `au moins ${n}` : n;
}

function fmtRate(r: Report): string {
  if (r.total_units === null || r.total_units === 0 || r.units_partial) return "—";
  const pct = Math.round((100 * (r.failing_units ?? 0)) / r.total_units);
  return `${pct} % en échec`;
}
