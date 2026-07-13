import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";

export interface Tenant {
  id: string;
  domain: string;
  name: string;
}

export interface MatchingRule {
  id: string;
  tenant_id: string;
  domain: string;
  rule_type: "sender" | "subject_regex" | "keyword" | "alias";
  pattern: string;
  priority: number;
  is_active: boolean;
}

export interface RuleTest {
  tenant_id: string | null;
  domain: string | null;
  method: string;
  confidence: number;
  quarantined: boolean;
}

export const useTenants = () =>
  useQuery({ queryKey: ["tenants"], queryFn: () => api<Tenant[]>("/admin/tenants") });

/* Les règles se lisent GLOBALEMENT, dans l'ordre d'évaluation : la cascade s'arrête au
   premier match, donc une règle vue isolément ne dit rien de ce qu'elle fait. */
export const useRules = () =>
  useQuery({ queryKey: ["rules"], queryFn: () => api<MatchingRule[]>("/admin/rules") });

const invalidate = (qc: ReturnType<typeof useQueryClient>) => () =>
  qc.invalidateQueries({ queryKey: ["rules"] });

export function useAddRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: {
      tenant_id: string;
      rule_type: string;
      pattern: string;
      priority: number;
    }) =>
      api<{ id: string }>("/admin/rules", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(b),
      }),
    onSuccess: invalidate(qc),
  });
}

export function useUpdateRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...b }: { id: string; is_active?: boolean; priority?: number }) =>
      api(`/admin/rules/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(b),
      }),
    onSuccess: invalidate(qc),
  });
}

export function useDeleteRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/admin/rules/${id}`, { method: "DELETE" }),
    onSuccess: invalidate(qc),
  });
}

export function useTestRules() {
  return useMutation({
    mutationFn: (b: { subject: string; from_address: string }) =>
      api<RuleTest>("/admin/rules/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(b),
      }),
  });
}
