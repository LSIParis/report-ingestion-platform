import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";

export interface Domain {
  id: string;
  domain: string;
  name: string;
  status: string;
  reports: number;
  last_report_at: string | null;
  active_rules: number;
  created_at: string;
}

export const useDomains = () =>
  useQuery({ queryKey: ["domains"], queryFn: () => api<Domain[]>("/admin/tenants") });

const invalidate = (qc: ReturnType<typeof useQueryClient>) => () => {
  qc.invalidateQueries({ queryKey: ["domains"] });
  qc.invalidateQueries({ queryKey: ["tenants"] }); // sélecteur du menu de compte
};

export function useCreateDomain() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { domain: string; name?: string }) =>
      api<Domain>("/admin/tenants", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(b),
      }),
    onSuccess: invalidate(qc),
  });
}

export function useUpdateDomain() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...b }: { id: string; name?: string; active?: boolean }) =>
      api<Domain>(`/admin/tenants/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(b),
      }),
    onSuccess: invalidate(qc),
  });
}

export function useDeleteDomain() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/admin/tenants/${id}`, { method: "DELETE" }),
    onSuccess: invalidate(qc),
  });
}

export function useRequeueQuarantine() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api<{ requeued: number }>("/admin/quarantine/requeue", { method: "POST" }),
    onSuccess: () => qc.invalidateQueries(),
  });
}
