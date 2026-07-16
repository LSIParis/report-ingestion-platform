import { useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  useConfirmEmailChange,
  useRequestEmailChange,
  useUpdateProfile,
} from "../api/account";
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

/** Convertit les `null` de l'API en `""` pour le formulaire. Accepte `Me` comme `User`. */
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

const IDENTITE: { cle: keyof ProfileValues; label: string; type?: string }[] = [
  { cle: "last_name", label: "Nom" },
  { cle: "first_name", label: "Prénom" },
  { cle: "company", label: "Société" },
  { cle: "address", label: "Adresse" },
  { cle: "phone", label: "Téléphone", type: "tel" },
];

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
        {mode === "admin" ? (
          <AdminForm userId={userId!} initial={initial} onClose={onClose} />
        ) : (
          <SelfForm initial={initial} onClose={onClose} />
        )}
      </div>
    </div>
  );
}

/* Admin : identite + e-mail, immediat (PATCH /admin/users/{id}). */
function AdminForm({
  userId,
  initial,
  onClose,
}: {
  userId: string;
  initial: ProfileValues;
  onClose: () => void;
}) {
  const update = useUpdateUser();
  const [v, setV] = useState<ProfileValues>(initial);
  const [error, setError] = useState("");
  const emailOk = /\S+@\S+/.test(v.email);

  async function save() {
    setError("");
    try {
      await update.mutateAsync({ id: userId, ...v });
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
    <div className="space-y-3">
      {IDENTITE.map((c) => (
        <Champ key={c.cle} label={c.label} type={c.type}
               value={v[c.cle]} onChange={(x) => setV((s) => ({ ...s, [c.cle]: x }))} />
      ))}
      <Champ label="E-mail" type="email" value={v.email}
             onChange={(x) => setV((s) => ({ ...s, email: x }))} />
      {error && <p className="text-sm text-red-600">{error}</p>}
      <Actions onCancel={onClose} onSave={save}
               disabled={!emailOk || update.isPending} pending={update.isPending} />
    </div>
  );
}

/* Self : identite immediate (PATCH /auth/me) + changement d'e-mail verifie par code. */
function SelfForm({ initial, onClose }: { initial: ProfileValues; onClose: () => void }) {
  const nav = useNavigate();
  const updateProfile = useUpdateProfile();
  const requestEmail = useRequestEmailChange();
  const confirmEmail = useConfirmEmailChange();

  const [v, setV] = useState<ProfileValues>(initial);
  const [error, setError] = useState("");

  const [etape, setEtape] = useState<"idle" | "email" | "code">("idle");
  const [newEmail, setNewEmail] = useState("");
  const [code, setCode] = useState("");
  const [emailErr, setEmailErr] = useState("");

  async function saveIdentite() {
    setError("");
    try {
      await updateProfile.mutateAsync({
        first_name: v.first_name,
        last_name: v.last_name,
        company: v.company,
        address: v.address,
        phone: v.phone,
      });
      onClose();
    } catch {
      setError("Enregistrement impossible.");
    }
  }

  async function envoyerCode() {
    setEmailErr("");
    try {
      await requestEmail.mutateAsync({ new_email: newEmail });
      setEtape("code");
    } catch (e) {
      setEmailErr(
        e instanceof ApiError && e.status === 409
          ? "Cet e-mail est déjà utilisé."
          : e instanceof ApiError && e.status === 502
            ? "Impossible d'envoyer le code, réessayez."
            : e instanceof ApiError && e.status === 400
              ? "C'est déjà votre adresse."
              : "Demande impossible.",
      );
    }
  }

  async function confirmer() {
    setEmailErr("");
    try {
      await confirmEmail.mutateAsync({ code });
      // L'e-mail (identifiant) a change -> le jeton porte l'ancien sub : reconnexion.
      clearSession();
      nav("/login", { replace: true });
    } catch (e) {
      setEmailErr(
        e instanceof ApiError && e.status === 429
          ? "Trop d'essais, redemandez un code."
          : e instanceof ApiError && e.status === 409
            ? "Cet e-mail vient d'être pris."
            : "Code incorrect ou expiré.",
      );
    }
  }

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        {IDENTITE.map((c) => (
          <Champ key={c.cle} label={c.label} type={c.type}
                 value={v[c.cle]} onChange={(x) => setV((s) => ({ ...s, [c.cle]: x }))} />
        ))}
        {error && <p className="text-sm text-red-600">{error}</p>}
        <Actions onCancel={onClose} onSave={saveIdentite}
                 disabled={updateProfile.isPending} pending={updateProfile.isPending}
                 label="Enregistrer l'identité" />
      </div>

      <div className="border-t pt-4">
        <div className="text-xs text-gray-600">E-mail de connexion</div>
        <div className="mt-1 flex items-center justify-between gap-2">
          <span className="text-sm">{initial.email}</span>
          {etape === "idle" && (
            <button
              onClick={() => {
                setNewEmail("");
                setEmailErr("");
                setEtape("email");
              }}
              className="text-xs text-blue-600 hover:underline"
            >
              Changer l'e-mail
            </button>
          )}
        </div>

        {etape === "email" && (
          <div className="mt-3 space-y-2">
            <Champ label="Nouvel e-mail" type="email" value={newEmail} onChange={setNewEmail} />
            <p className="text-xs text-amber-700">
              Un code sera envoyé à cette adresse pour la vérifier. Après confirmation, vous
              serez déconnecté et vous reconnecterez avec la nouvelle adresse.
            </p>
            {emailErr && <p className="text-sm text-red-600">{emailErr}</p>}
            <div className="flex gap-2">
              <button onClick={() => setEtape("idle")} className="rounded border px-3 py-1.5 text-sm">
                Annuler
              </button>
              <button
                onClick={envoyerCode}
                disabled={!/\S+@\S+/.test(newEmail) || requestEmail.isPending}
                className="rounded bg-gray-900 px-3 py-1.5 text-sm text-white disabled:opacity-40"
              >
                {requestEmail.isPending ? "…" : "Envoyer le code"}
              </button>
            </div>
          </div>
        )}

        {etape === "code" && (
          <div className="mt-3 space-y-2">
            <p className="text-sm">
              Un code a été envoyé à <strong>{newEmail}</strong>.
            </p>
            <Champ label="Code (6 chiffres)" value={code} onChange={setCode} />
            {emailErr && <p className="text-sm text-red-600">{emailErr}</p>}
            <div className="flex gap-2">
              <button onClick={envoyerCode} disabled={requestEmail.isPending}
                      className="rounded border px-3 py-1.5 text-sm">
                Renvoyer le code
              </button>
              <button
                onClick={confirmer}
                disabled={code.length < 6 || confirmEmail.isPending}
                className="rounded bg-gray-900 px-3 py-1.5 text-sm text-white disabled:opacity-40"
              >
                {confirmEmail.isPending ? "…" : "Confirmer"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Champ({
  label,
  type,
  value,
  onChange,
}: {
  label: string;
  type?: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label className="block">
      <span className="text-xs text-gray-600">{label}</span>
      <input
        type={type ?? "text"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full rounded border px-3 py-2 text-sm"
      />
    </label>
  );
}

function Actions({
  onCancel,
  onSave,
  disabled,
  pending,
  label,
}: {
  onCancel: () => void;
  onSave: () => void;
  disabled: boolean;
  pending: boolean;
  label?: string;
}) {
  return (
    <div className="flex gap-2">
      <button onClick={onCancel} className="flex-1 rounded border py-2 text-sm">
        Annuler
      </button>
      <button
        onClick={onSave}
        disabled={disabled}
        className="flex-1 rounded bg-gray-900 py-2 text-sm text-white disabled:opacity-40"
      >
        {pending ? "…" : label ?? "Enregistrer"}
      </button>
    </div>
  );
}
