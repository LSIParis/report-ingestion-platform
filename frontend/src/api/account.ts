import { useMutation, useQuery } from "@tanstack/react-query";

import { api } from "./client";

export interface Me {
  email: string;
  role: string;
  tenants: { id: string; domain: string; name: string }[];
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
