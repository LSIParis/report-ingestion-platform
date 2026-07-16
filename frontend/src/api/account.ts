import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";

export interface Me {
  email: string;
  role: string;
  tenants: { id: string; domain: string; name: string }[];
  first_name: string | null;
  last_name: string | null;
  company: string | null;
  address: string | null;
  phone: string | null;
  pending_email: string | null;
}

/* Le JWT ne porte que des UUID de domaines. /auth/me les résout en noms lisibles,
   à partir des tenant_ids du jeton SIGNÉ — un utilisateur ne peut donc pas s'ajouter
   un domaine en forgeant la requête. */
export const useMe = () =>
  useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/auth/me"), staleTime: 5 * 60_000 });

export const useChangePassword = () =>
  useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) =>
      api<void>("/auth/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
  });

export function useUpdateProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      first_name: string; last_name: string; company: string; address: string; phone: string;
    }) =>
      api<void>("/auth/me", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["me"] }),
  });
}

export const useRequestEmailChange = () =>
  useMutation({
    mutationFn: (body: { new_email: string }) =>
      api<void>("/auth/me/email/request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
  });

export const useConfirmEmailChange = () =>
  useMutation({
    mutationFn: (body: { code: string }) =>
      api<void>("/auth/me/email/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
  });
