import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "../api/client";

const COLORS: Record<string, string> = { ok: "#16a34a", partial: "#ea580c", failed: "#dc2626" };

interface Row {
  brand?: string;
  bucket?: string;
  status: string;
  count: number;
}

export function Metrics() {
  const byBrand = useQuery({ queryKey: ["metrics", "brand"], queryFn: () => api<Row[]>("/metrics/by-brand") });
  const ts = useQuery({ queryKey: ["metrics", "ts", "day"], queryFn: () => api<Row[]>("/metrics/timeseries?granularity=day") });

  return (
    <div className="p-6 space-y-8">
      <h1 className="text-xl font-semibold">Métriques</h1>

      <section>
        <h2 className="font-medium mb-2">Rapports par marque et statut</h2>
        <div className="h-72 bg-white rounded border p-4">
          <ResponsiveContainer>
            <BarChart data={pivot(byBrand.data ?? [], "brand")}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="key" />
              <YAxis />
              <Tooltip />
              <Legend />
              <Bar dataKey="ok" stackId="a" fill={COLORS.ok} />
              <Bar dataKey="partial" stackId="a" fill={COLORS.partial} />
              <Bar dataKey="failed" stackId="a" fill={COLORS.failed} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section>
        <h2 className="font-medium mb-2">Volume dans le temps</h2>
        <div className="h-72 bg-white rounded border p-4">
          <ResponsiveContainer>
            <LineChart data={pivot(ts.data ?? [], "bucket")}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="key" tickFormatter={(d) => new Date(d).toLocaleDateString()} />
              <YAxis />
              <Tooltip />
              <Legend />
              <Line dataKey="ok" stroke={COLORS.ok} />
              <Line dataKey="partial" stroke={COLORS.partial} />
              <Line dataKey="failed" stroke={COLORS.failed} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </section>
    </div>
  );
}

function pivot(rows: Row[], field: "brand" | "bucket") {
  const m: Record<string, Record<string, number | string>> = {};
  for (const r of rows) {
    const k = String(r[field] ?? "—");
    (m[k] ??= { key: k })[r.status] = r.count;
  }
  return Object.values(m);
}
