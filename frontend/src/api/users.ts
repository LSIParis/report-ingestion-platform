import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";

export interface User {
  id: string;
  email: string;
  role: "platform_admin" | "tenant_viewer";
  tenants: { id: string; domain: string }[];
  created_at: string;
}

export const useUsers = () =>
  useQuery({ queryKey: ["users"], queryFn: () => api<User[]>("/admin/users") });

const invalidate = (qc: ReturnType<typeof useQueryClient>) => () =>
  qc.invalidateQueries({ queryKey: ["users"] });

export function useCreateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { email: string; role: string; password: string; tenant_ids: string[] }) =>
      api<User>("/admin/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(b),
      }),
    onSuccess: invalidate(qc),
  });
}

export function useUpdateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...b }: { id: string; role?: string; tenant_ids?: string[] }) =>
      api<User>(`/admin/users/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(b),
      }),
    onSuccess: invalidate(qc),
  });
}

export function useResetPassword() {
  return useMutation({
    mutationFn: ({ id, password }: { id: string; password: string }) =>
      api<void>(`/admin/users/${id}/password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ new_password: password }),
      }),
  });
}

export function useDeleteUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/admin/users/${id}`, { method: "DELETE" }),
    onSuccess: invalidate(qc),
  });
}

/* Mot de passe généré côté navigateur via l'API crypto : ni prévisible, ni transmis
   ailleurs que dans la requête de création. */
export function generatePassword(length = 20): string {
  const alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const bytes = crypto.getRandomValues(new Uint32Array(length));
  return Array.from(bytes, (b) => alphabet[b % alphabet.length]).join("");
}
