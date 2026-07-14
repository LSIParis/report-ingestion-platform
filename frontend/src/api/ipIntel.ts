import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";

export interface Sender {
  key: string;
  name: string;
  spf_include: string | null;
  remediation: string;
}

export interface IpIntel {
  ip: string;
  ptr: string | null;
  fcrdns: boolean;
  asn: number | null;
  as_org: string | null;
  country: string | null;
  checked_at: string;
  sender: Sender | null;
  hosted_by: string | null;
  spf: {
    result: "pass" | "fail" | "softfail" | "neutral" | "none" | "permerror" | "indetermine";
    mechanism: string | null;
  };
  activity: {
    messages: number;
    rows: number;
    first_seen: string | null;
    last_seen: string | null;
    dispositions: Record<string, number>;
    aligned: Record<string, number>;
    spf_domains: string[];
    dkim_domains: string[];
    header_froms: string[];
    // Somme des sessions TLS en échec vues depuis cette IP. `null` = magnitude
    // inconnue (au moins un échec observé, mais aucun compteur lisible) ; `0` = aucune
    // ligne TLS pour cette IP (vrai zéro, pas un défaut). Ne JAMAIS confondre les deux.
    tls_sessions: number | null;
    // `true` = tls_sessions (s'il n'est pas null) est un MINORANT : au moins une
    // occurrence illisible existe en plus de celles comptées.
    tls_partial: boolean;
    // Détail par type d'échec (ex. "certificate-host-mismatch"), même convention
    // sessions/partial que ci-dessus.
    tls_failures: Record<string, { sessions: number | null; partial: boolean }>;
  };
}

export const useIpIntel = (ip: string | null) =>
  useQuery({
    queryKey: ["ip-intel", ip],
    queryFn: () => api<IpIntel>(`/ip-intel/${ip}`),
    enabled: ip !== null,
  });

export function useRefreshIpIntel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ip: string) => api<IpIntel>(`/ip-intel/${ip}/refresh`, { method: "POST" }),
    onSuccess: (data) => qc.setQueryData(["ip-intel", data.ip], data),
  });
}
