import { useQuery } from "@tanstack/react-query";

import { api } from "./client";

export interface Alert {
  id: string;
  domain: string;
  kind: "tls_failure" | "domain_silent" | "never_reported";
  severity: "warning" | "critical";
  dedup_key: string;
  payload: Record<string, unknown>;
  opened_at: string;
  closed_at: string | null;
  // `notified_at` a été scindée en deux (migration 0008) : une alerte est notifiée à
  // deux moments distincts et légitimes de sa vie (ouverture, puis fermeture).
  opened_notified_at: string | null;
  closed_notified_at: string | null;
}

export const useAlerts = (status: "open" | "all") =>
  useQuery({
    queryKey: ["alerts", status],
    queryFn: () => api<Alert[]>(`/admin/alerts?status=${status}`),
  });
