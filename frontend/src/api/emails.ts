import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type { Page } from "./reports";

export interface Email {
  id: string;
  tenant_id: string | null;
  from_address: string;
  subject: string;
  status: string;
  resolved_by: string | null;
  received_at: string;
}

export const useQuarantine = (page: number) =>
  useQuery({
    queryKey: ["quarantine", page],
    queryFn: () => api<Page<Email>>(`/emails/queue/quarantine?page=${page}`),
    refetchInterval: 30_000,
  });

export function useAssignTenant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, tenant_id }: { id: string; tenant_id: string }) =>
      api(`/emails/${id}/assign-tenant`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tenant_id }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["quarantine"] }),
  });
}
