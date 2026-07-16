import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { useUpdateProfile } from "../api/account";
import { ApiError } from "../api/client";
import { useUpdateUser } from "../api/users";
import { clearSession } from "../auth/session";

export interface ProfileValues {
  email: string;
  first_name: string;
  last_name: string;
  company: string;
  address: string;
  phone: string;
}

/** Convertit les `null` de l'API en `""` pour le formulaire. */
export function toProfileValues(u: {
  email: string; first_name: string | null; last_name: string | null;
  company: string | null; address: string | null; phone: string | null;
}): ProfileValues {
  return {
    email: u.email,
    first_name: u.first_name ?? "",
    last_name: u.last_name ?? "",
    company: u.company ?? "",
    address: u.address ?? "",
    phone: u.phone ?? "",
  };
}

const CHAMPS: { cle: keyof ProfileValues; label: string; type?: string }[] = [
  { cle: "last_name", label: "Nom" },
  { cle: "first_name", label: "Prénom" },
  { cle: "company", label: "Société" },
  { cle: "address", label: "Adresse" },
  { cle: "phone", label: "Téléphone", type: "tel" },
  { cle: "email", label: "E-mail", type: "email" },
];

/** Fiche d'identite. `mode="self"` -> PATCH /auth/me (et reconnexion si l'e-mail change) ;
 *  `mode="admin"` -> PATCH /admin/users/{userId}. */
export function ProfileDialog({
  mode,
  userId,
  initial,
  onClose,
}: {
  mode: "self" | "admin";
  userId?: string;
  initial: ProfileValues;
  onClose: () => void;
}) {
  const nav = useNavigate();
  const self = useUpdateProfile();
  const admin = useUpdateUser();
  const [v, setV] = useState<ProfileValues>(initial);
  const [error, setError] = useState("");

  const emailOk = /\S+@\S+/.test(v.email);
  const pending = self.isPending || admin.isPending;

  async function save() {
    setError("");
    try {
      if (mode === "self") {
        await self.mutateAsync(v);
        // L'e-mail (identifiant) a change -> le jeton porte l'ancien sub : on se reconnecte.
        if (v.email.trim().toLowerCase() !== initial.email.trim().toLowerCase()) {
          clearSession();
          nav("/login", { replace: true });
          return;
        }
      } else {
        await admin.mutateAsync({ id: userId!, ...v });
      }
      onClose();
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 409
          ? "Cet e-mail est déjà utilisé."
          : "Enregistrement impossible.",
      );
    }
  }

  return (
    <div
      className="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-4"
      onMouseDown={onClose}
    >
      <div
        className="w-full max-w-md rounded border bg-white p-6"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <h2 className="mb-4 font-semibold">Fiche — {initial.email}</h2>
        <div className="space-y-3">
          {CHAMPS.map((c) => (
            <label key={c.cle} className="block">
              <span className="text-xs text-gray-600">{c.label}</span>
              <input
                type={c.type ?? "text"}
                value={v[c.cle]}
                onChange={(e) => setV((s) => ({ ...s, [c.cle]: e.target.value }))}
                className="mt-1 w-full rounded border px-3 py-2 text-sm"
              />
            </label>
          ))}
        </div>

        {mode === "self" &&
          v.email.trim().toLowerCase() !== initial.email.trim().toLowerCase() && (
            <p className="mt-3 text-xs text-amber-700">
              Changer votre e-mail vous déconnectera : vous vous reconnecterez avec la
              nouvelle adresse.
            </p>
          )}

        {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

        <div className="mt-4 flex gap-2">
          <button onClick={onClose} className="flex-1 rounded border py-2 text-sm">
            Annuler
          </button>
          <button
            onClick={save}
            disabled={!emailOk || pending}
            className="flex-1 rounded bg-gray-900 py-2 text-sm text-white disabled:opacity-40"
          >
            {pending ? "…" : "Enregistrer"}
          </button>
        </div>
      </div>
    </div>
  );
}
