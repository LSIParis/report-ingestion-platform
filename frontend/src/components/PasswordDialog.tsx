import { useState } from "react";

import { useChangePassword } from "../api/account";
import { ApiError } from "../api/client";

export function PasswordDialog({
  minLength,
  onClose,
}: {
  minLength: number;
  onClose: () => void;
}) {
  const change = useChangePassword();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  const tooShort = next.length > 0 && next.length < minLength;
  const mismatch = confirm.length > 0 && next !== confirm;
  const ready = current && next.length >= minLength && next === confirm;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await change.mutateAsync({ current_password: current, new_password: next });
      setDone(true);
    } catch (err) {
      // 403 = le mot de passe actuel est faux. On le dit, plutôt qu'un « erreur »
      // générique qui laisse l'utilisateur deviner ce qu'il doit corriger.
      setError(
        err instanceof ApiError && err.status === 403
          ? "Votre mot de passe actuel est incorrect."
          : "Le changement a échoué. Réessayez.",
      );
    }
  }

  return (
    <div
      className="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-4"
      onMouseDown={onClose}
    >
      <div
        className="w-full max-w-sm rounded border bg-white p-6"
        onMouseDown={(e) => e.stopPropagation()}
      >
        {done ? (
          <div className="space-y-4">
            <h2 className="font-semibold">Mot de passe modifié</h2>
            <p className="text-sm text-gray-600">
              Votre nouveau mot de passe est actif. Votre session en cours reste ouverte.
            </p>
            <button
              onClick={onClose}
              className="w-full rounded bg-gray-900 py-2 text-sm text-white"
            >
              Fermer
            </button>
          </div>
        ) : (
          <form onSubmit={submit} className="space-y-4">
            <h2 className="font-semibold">Changer mon mot de passe</h2>

            <Field
              label="Mot de passe actuel"
              value={current}
              onChange={setCurrent}
              autoFocus
            />
            <Field
              label={`Nouveau mot de passe (${minLength} caractères minimum)`}
              value={next}
              onChange={setNext}
              hint={tooShort ? `Encore ${minLength - next.length} caractère(s).` : undefined}
            />
            <Field
              label="Confirmer"
              value={confirm}
              onChange={setConfirm}
              hint={mismatch ? "Les deux saisies diffèrent." : undefined}
            />

            {error && <p className="text-sm text-red-600">{error}</p>}

            <div className="flex gap-2">
              <button
                type="button"
                onClick={onClose}
                className="flex-1 rounded border py-2 text-sm"
              >
                Annuler
              </button>
              <button
                disabled={!ready || change.isPending}
                className="flex-1 rounded bg-gray-900 py-2 text-sm text-white disabled:opacity-40"
              >
                {change.isPending ? "…" : "Enregistrer"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  hint,
  autoFocus,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  hint?: string;
  autoFocus?: boolean;
}) {
  return (
    <label className="block">
      <span className="text-xs text-gray-600">{label}</span>
      <input
        type="password"
        autoFocus={autoFocus}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full rounded border px-3 py-2 text-sm"
      />
      {hint && <span className="mt-1 block text-xs text-amber-700">{hint}</span>}
    </label>
  );
}
