import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { api } from "../api/client";

interface Summary {
  total: number;
  parsed_ok: number;
  parsed_partial: number;
  failed: number;
  needs_review: number;
}

export function Overview() {
  const summary = useQuery({ queryKey: ["metrics", "summary"], queryFn: () => api<Summary>("/metrics/summary") });
  const ts = useQuery({
    queryKey: ["metrics", "ts"],
    queryFn: () => api<{ bucket: string; count: number }[]>("/metrics/timeseries?granularity=day"),
  });

  const s = summary.data;
  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold mb-4">Vue d'ensemble</h1>
      <div className="grid grid-cols-5 gap-4 mb-6">
        <Kpi label="Total" value={s?.total} />
        <Kpi label="OK" value={s?.parsed_ok} tone="text-green-600" />
        <Kpi label="Partiels" value={s?.parsed_partial} tone="text-orange-600" />
        <Kpi label="Échecs" value={s?.failed} tone="text-red-600" />
        <Kpi label="À revoir" value={s?.needs_review} tone="text-gray-500" />
      </div>
      <div className="h-64 bg-white rounded border p-4">
        <ResponsiveContainer>
          <BarChart data={ts.data ?? []}>
            <XAxis dataKey="bucket" tickFormatter={(b) => new Date(b).toLocaleDateString()} />
            <YAxis />
            <Tooltip />
            <Bar dataKey="count" fill="#2563eb" />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function Kpi({ label, value, tone }: { label: string; value?: number; tone?: string }) {
  return (
    <div className="bg-white rounded border p-4">
      <div className="text-sm text-gray-500">{label}</div>
      <div className={`text-2xl font-semibold ${tone ?? ""}`}>{value ?? "—"}</div>
    </div>
  );
}
