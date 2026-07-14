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
