import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "../api/client";
import { IpPanel } from "../components/IpPanel";

/* La page d'accueil répond à la question du client : « qui envoie du courrier en mon nom,
   et combien de ces messages échouent à l'authentification ? ». On raisonne donc en
   MESSAGES (message_count), pas en rapports : une ligne de rapport peut valoir 1 message
   comme 12 000. */

interface Summary {
  days: number;
  messages: number;
  compliant: number;
  failing: number;
  compliance_rate: number | null;
  dkim_pass: number;
  spf_pass: number;
  quarantined: number;
  rejected: number;
  sources: number;
  failing_sources: number;
}

interface Point {
  day: string;
  compliant: number;
  failing: number;
}

interface Source {
  source_ip: string;
  messages: number;
  compliant: number;
  failing: number;
  compliance_rate: number | null;
  last_seen: string | null;
  reporter: string | null;
}

const OK = "#059669";
const KO = "#dc2626";
const RANGES = [7, 30, 90] as const;

export function Overview() {
  const [days, setDays] = useState<number>(30);
  // L'IP source est le point d'entree de l'enquete : cliquable, elle ouvre le meme
  // panneau lateral que la page Rapports (IpPanel). null = panneau ferme.
  const [ip, setIp] = useState<string | null>(null);

  const summary = useQuery({
    queryKey: ["dmarc", "summary", days],
    queryFn: () => api<Summary>(`/metrics/dmarc/summary?days=${days}`),
  });
  const series = useQuery({
    queryKey: ["dmarc", "ts", days],
    queryFn: () => api<Point[]>(`/metrics/dmarc/timeseries?days=${days}`),
  });
  const sources = useQuery({
    queryKey: ["dmarc", "sources", days],
    queryFn: () => api<Source[]>(`/metrics/dmarc/sources?days=${days}&limit=12`),
  });

  const s = summary.data;
  const rate = s?.compliance_rate ?? null;

  return (
    <div className="p-6 space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Authentification du courrier</h1>
          <p className="text-sm text-gray-500">
            Ce que les fournisseurs de messagerie ont constaté sur les messages envoyés en votre nom.
          </p>
        </div>
        <div className="flex rounded border bg-white text-sm">
          {RANGES.map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`px-3 py-1.5 ${
                days === d ? "bg-gray-900 text-white" : "text-gray-600 hover:bg-gray-100"
              }`}
            >
              {d} j
            </button>
          ))}
        </div>
      </header>

      {summary.isSuccess && s!.messages === 0 ? (
        <Empty days={days} />
      ) : (
        <>
          {/* Chiffres clés — le taux d'abord : c'est la seule question qui compte. */}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Kpi
              label="Messages authentifiés"
              value={rate === null ? "—" : `${rate} %`}
              hint={s ? `${fmt(s.compliant)} sur ${fmt(s.messages)} messages` : undefined}
              tone={rate === null ? "" : rate >= 98 ? "text-emerald-600" : rate >= 90 ? "text-amber-600" : "text-red-600"}
            />
            <Kpi
              label="Messages en échec"
              value={s ? fmt(s.failing) : "—"}
              hint="ni DKIM ni SPF aligné"
              tone={s && s.failing > 0 ? "text-red-600" : ""}
            />
            <Kpi
              label="Sources d'envoi"
              value={s ? fmt(s.sources) : "—"}
              hint={s ? `${s.failing_sources} n'authentifient rien` : undefined}
              tone={s && s.failing_sources > 0 ? "text-amber-600" : ""}
            />
            <Kpi
              label="Messages bloqués"
              value={s ? fmt(s.quarantined + s.rejected) : "—"}
              hint={s ? `${fmt(s.quarantined)} en indésirables · ${fmt(s.rejected)} rejetés` : undefined}
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            {/* Volume dans le temps : l'échec doit sauter aux yeux, donc empilé en rouge. */}
            <section className="lg:col-span-2 rounded border bg-white p-4">
              <h2 className="text-sm font-medium text-gray-700">Volume quotidien</h2>
              <p className="mb-3 text-xs text-gray-500">
                Messages vus par les fournisseurs, par jour.
              </p>
              <div className="h-64">
                <ResponsiveContainer>
                  <AreaChart data={series.data ?? []} margin={{ left: -18, right: 4, top: 4 }}>
                    <CartesianGrid stroke="#f1f5f9" vertical={false} />
                    <XAxis
                      dataKey="day"
                      tickFormatter={shortDate}
                      tick={{ fontSize: 11, fill: "#94a3b8" }}
                      tickLine={false}
                      axisLine={{ stroke: "#e2e8f0" }}
                      minTickGap={24}
                    />
                    <YAxis
                      tick={{ fontSize: 11, fill: "#94a3b8" }}
                      tickLine={false}
                      axisLine={false}
                      width={48}
                    />
                    <Tooltip
                      labelFormatter={(d) => longDate(String(d))}
                      formatter={(v: number, name) => [
                        fmt(v),
                        name === "compliant" ? "Authentifiés" : "En échec",
                      ]}
                      contentStyle={{ fontSize: 12, borderRadius: 4, border: "1px solid #e2e8f0" }}
                    />
                    <Area
                      type="monotone"
                      dataKey="compliant"
                      stackId="1"
                      stroke={OK}
                      fill={OK}
                      fillOpacity={0.14}
                    />
                    <Area
                      type="monotone"
                      dataKey="failing"
                      stackId="1"
                      stroke={KO}
                      fill={KO}
                      fillOpacity={0.22}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
              <Legend />
            </section>

            {/* DKIM et SPF séparément : DMARC passe si l'UN des deux est aligné, donc
                savoir lequel est cassé dit quoi réparer. */}
            <section className="rounded border bg-white p-4">
              <h2 className="text-sm font-medium text-gray-700">Répartition</h2>
              <p className="mb-3 text-xs text-gray-500">
                Un message est authentifié si DKIM <em>ou</em> SPF est aligné.
              </p>
              <div className="h-40">
                <ResponsiveContainer>
                  <PieChart>
                    <Pie
                      data={[
                        { name: "Authentifiés", value: s?.compliant ?? 0 },
                        { name: "En échec", value: s?.failing ?? 0 },
                      ]}
                      dataKey="value"
                      innerRadius={44}
                      outerRadius={64}
                      paddingAngle={2}
                      stroke="none"
                    >
                      <Cell fill={OK} />
                      <Cell fill={KO} />
                    </Pie>
                    <Tooltip
                      formatter={(v: number) => fmt(v)}
                      contentStyle={{ fontSize: 12, borderRadius: 4, border: "1px solid #e2e8f0" }}
                    />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div className="mt-2 space-y-2">
                <Meter label="DKIM aligné" value={s?.dkim_pass ?? 0} total={s?.messages ?? 0} />
                <Meter label="SPF aligné" value={s?.spf_pass ?? 0} total={s?.messages ?? 0} />
              </div>
            </section>
          </div>

          {/* Le tableau est la partie actionnable : c'est là qu'on voit QUOI réparer. */}
          <section className="rounded border bg-white">
            <div className="border-b p-4">
              <h2 className="text-sm font-medium text-gray-700">Sources d'envoi</h2>
              <p className="text-xs text-gray-500">
                Trié par volume. Une source qui n'authentifie rien est soit un outil légitime mal
                configuré, soit une usurpation.
              </p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wide text-gray-500">
                    <th className="px-4 py-2 font-medium">Adresse IP</th>
                    <th className="px-4 py-2 font-medium">Constaté par</th>
                    <th className="px-4 py-2 text-right font-medium">Messages</th>
                    <th className="px-4 py-2 font-medium">Authentification</th>
                    <th className="px-4 py-2 font-medium">Dernier envoi</th>
                  </tr>
                </thead>
                <tbody>
                  {(sources.data ?? []).map((r) => (
                    <tr key={r.source_ip} className="border-t">
                      <td className="px-4 py-2">
                        <button
                          onClick={() => setIp(r.source_ip)}
                          className="font-mono text-xs text-blue-600 hover:underline"
                        >
                          {r.source_ip}
                        </button>
                      </td>
                      <td className="px-4 py-2 text-gray-500">{r.reporter ?? "—"}</td>
                      <td className="px-4 py-2 text-right tabular-nums">{fmt(r.messages)}</td>
                      <td className="px-4 py-2">
                        <RateBar rate={r.compliance_rate} failing={r.failing} />
                      </td>
                      <td className="px-4 py-2 text-gray-500">
                        {r.last_seen ? shortDate(r.last_seen) : "—"}
                      </td>
                    </tr>
                  ))}
                  {sources.isSuccess && sources.data!.length === 0 && (
                    <tr>
                      <td colSpan={5} className="px-4 py-6 text-center text-gray-500">
                        Aucune source sur la période.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}

      {ip && <IpPanel ip={ip} onClose={() => setIp(null)} />}
    </div>
  );
}

function Kpi({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: string;
}) {
  return (
    <div className="rounded border bg-white p-4">
      <div className="text-xs uppercase tracking-wide text-gray-500">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${tone ?? ""}`}>{value}</div>
      {hint && <div className="mt-1 text-xs text-gray-500">{hint}</div>}
    </div>
  );
}

function Meter({ label, value, total }: { label: string; value: number; total: number }) {
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

function RateBar({ rate, failing }: { rate: number | null; failing: number }) {
  if (rate === null) return <span className="text-gray-400">—</span>;
  const ok = rate >= 98;
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 rounded bg-red-100">
        <div
          className={`h-1.5 rounded ${ok ? "bg-emerald-500" : "bg-amber-500"}`}
          style={{ width: `${rate}%` }}
        />
      </div>
      <span className="tabular-nums text-xs text-gray-600">{rate} %</span>
      {failing > 0 && (
        <span className="rounded bg-red-50 px-1.5 py-0.5 text-xs text-red-700">
          {fmt(failing)} en échec
        </span>
      )}
    </div>
  );
}

function Legend() {
  return (
    <div className="mt-2 flex gap-4 text-xs text-gray-600">
      <span className="flex items-center gap-1.5">
        <i className="h-2 w-2 rounded-full" style={{ background: OK }} /> Authentifiés
      </span>
      <span className="flex items-center gap-1.5">
        <i className="h-2 w-2 rounded-full" style={{ background: KO }} /> En échec
      </span>
    </div>
  );
}

function Empty({ days }: { days: number }) {
  return (
    <div className="rounded border border-dashed bg-white p-10 text-center">
      <p className="font-medium">Aucun message sur les {days} derniers jours.</p>
      <p className="mt-1 text-sm text-gray-500">
        Les fournisseurs envoient un rapport par jour, et seulement si du courrier a circulé.
        Comptez 24 à 48 h après la publication de l'enregistrement DMARC.
      </p>
    </div>
  );
}

const nf = new Intl.NumberFormat("fr-FR");
const fmt = (n: number) => nf.format(n);
const shortDate = (d: string) =>
  new Date(d).toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" });
const longDate = (d: string) =>
  new Date(d).toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
