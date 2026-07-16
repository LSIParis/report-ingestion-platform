import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";

export interface ApiKey {
  id: string;
  scope: "platform" | "domain";
  tenant_id: string | null;
  domain: string | null;
  prefix: string;
  label: string;
  created_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface CreatedApiKey extends ApiKey {
  secret: string; // rendu une seule fois, à la création
}

export const useApiKeys = () =>
  useQuery({ queryKey: ["api-keys"], queryFn: () => api<ApiKey[]>("/admin/api-keys") });

export function useCreateApiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { scope: string; tenant_id?: string; label: string }) =>
      api<CreatedApiKey>("/admin/api-keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(b),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["api-keys"] }),
  });
}

export function useRevokeApiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/admin/api-keys/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["api-keys"] }),
  });
}
