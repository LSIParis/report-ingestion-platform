import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";

export interface Report {
  id: string;
  email_id: string;
  source_type: string;
  status: "ok" | "partial" | "failed";
  profile_id: string | null;
  row_count: number;
  parsed_at: string | null;
  created_at: string;
  kind: "dmarc" | "tls";
  reporter: string | null;
  total_units: number | null;
  failing_units: number | null;
  units_partial: boolean;
  period_start: string | null;
  period_end: string | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
}

export interface ParsingError {
  id: string;
  severity: string;
  code: string;
  message: string;
  context: { field?: string; row_index?: number } | null;
  created_at: string;
}

/** L'enveloppe RÉELLE renvoyée par `GET /reports/{id}/rows` (voir `ReportRowOut` côté
 *  backend) : les clés métier (source_ip, kind, policy_domain…) vivent dans `data`,
 *  jamais à la racine. `Page<Record<string, unknown>>` ne décrivait PAS cette forme —
 *  c'est ce qui a laissé passer le bug où `RowsTable` lisait `r.source_ip` à la racine
 *  au lieu de `r.data.source_ip` : les tables ne s'affichaient jamais, et TypeScript
 *  n'a rien dit puisque `Record<string, unknown>` accepte n'importe quelle clé. Avec
 *  cette enveloppe explicite, accéder à une clé hors de `id`/`report_date`/`data`
 *  redevient une erreur de type. */
export interface ReportRowEnvelope {
  id: string;
  report_date: string | null;
  data: Record<string, unknown>;
}

export function useReports(filters: { status?: string; brand?: string; kind?: string; page: number }) {
  const qs = new URLSearchParams();
  if (filters.status) qs.set("status_f", filters.status);
  if (filters.brand) qs.set("brand", filters.brand);
  if (filters.kind) qs.set("kind", filters.kind);
  qs.set("page", String(filters.page));
  return useQuery({
    queryKey: ["reports", filters],
    queryFn: () => api<Page<Report>>(`/reports?${qs}`),
    placeholderData: (prev) => prev,
  });
}

export const useReport = (id: string) =>
  useQuery({ queryKey: ["report", id], queryFn: () => api<Report>(`/reports/${id}`) });

export const useReportRows = (id: string, page: number) =>
  useQuery({
    queryKey: ["report", id, "rows", page],
    queryFn: () => api<Page<ReportRowEnvelope>>(`/reports/${id}/rows?page=${page}`),
    placeholderData: (prev) => prev,
  });

export const useReportErrors = (id: string) =>
  useQuery({
    queryKey: ["report", id, "errors"],
    queryFn: () => api<ParsingError[]>(`/reports/${id}/errors`),
  });

export function useReprocess() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api(`/reports/${id}/reprocess`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["reports"] }),
  });
}
