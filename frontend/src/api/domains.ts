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

export interface Step {
  key: string;
  title: string;
  why: string;
  zone: string;
  status: "ok" | "todo" | "warn" | "unknown";
  detail: string;
  record: { type: string; name: string; value: string } | null;
  found: string | null;
}

export interface Onboarding {
  domain: string;
  mx: string[];
  mx_policy: string[];
  steps: Step[];
}

export const useDomains = () =>
  useQuery({ queryKey: ["domains"], queryFn: () => api<Domain[]>("/admin/tenants") });

/* La procédure est VÉRIFIÉE côté serveur à chaque appel (résolution DNS en direct).
   On ne la met donc pas en cache : une procédure périmée est pire qu'aucune. */
export const useOnboarding = (id: string | null) =>
  useQuery({
    queryKey: ["onboarding", id],
    queryFn: () => api<Onboarding>(`/admin/tenants/${id}/onboarding`),
    enabled: !!id,
    gcTime: 0,
    staleTime: 0,
  });

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

export interface MtaSts {
  mode: "none" | "testing" | "enforce";
  max_age: number;
  mx: string[];
  policy_id: string;
  detected_mx: string[]; // déduit du MX réel : doit correspondre au certificat du MX
  preview: string;
}

export const useMtaSts = (id: string | null) =>
  useQuery({
    queryKey: ["mta-sts", id],
    queryFn: () => api<MtaSts>(`/admin/tenants/${id}/mta-sts`),
    enabled: !!id,
    gcTime: 0,
  });

export function useSaveMtaSts(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { mode: string; max_age: number; mx: string[] }) =>
      api<MtaSts>(`/admin/tenants/${id}/mta-sts`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(b),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["mta-sts", id] });
      qc.invalidateQueries({ queryKey: ["onboarding", id] });
    },
  });
}

export function useRequeueQuarantine() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api<{ requeued: number }>("/admin/quarantine/requeue", { method: "POST" }),
    onSuccess: () => qc.invalidateQueries(),
  });
}
