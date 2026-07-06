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
  rule_type: string;
  pattern: string;
  priority: number;
  is_active: boolean;
}

export const useTenants = () =>
  useQuery({ queryKey: ["tenants"], queryFn: () => api<Tenant[]>("/admin/tenants") });

export const useRules = (tenantId: string) =>
  useQuery({
    queryKey: ["rules", tenantId],
    queryFn: () => api<MatchingRule[]>(`/admin/tenants/${tenantId}/matching-rules`),
    enabled: !!tenantId,
  });

export function useAddRule(tenantId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (r: { rule_type: string; pattern: string; priority: number }) => {
      const qs = new URLSearchParams(r as unknown as Record<string, string>).toString();
      return api(`/admin/tenants/${tenantId}/matching-rules?${qs}`, { method: "POST" });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rules", tenantId] }),
  });
}
