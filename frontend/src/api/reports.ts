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

export function useReports(filters: { status?: string; brand?: string; page: number }) {
  const qs = new URLSearchParams();
  if (filters.status) qs.set("status_f", filters.status);
  if (filters.brand) qs.set("brand", filters.brand);
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
    queryFn: () => api<Page<Record<string, unknown>>>(`/reports/${id}/rows?page=${page}`),
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
