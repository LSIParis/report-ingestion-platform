import { useState } from "react";

import { useMe } from "../api/account";
import { useTenants } from "../api/admin";
import { ApiError } from "../api/client";
import {
  generatePassword,
  useCreateUser,
  useDeleteUser,
  useResetPassword,
  useUpdateUser,
  useUsers,
  type User,
} from "../api/users";
import { ProfileDialog, toProfileValues } from "../components/ProfileDialog";

export function Settings() {
  const me = useMe();
  const users = useUsers();
  const tenants = useTenants();
  const [creating, setCreating] = useState(false);

  return (
    <div className="space-y-6 p-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Paramètres — comptes</h1>
          <p className="text-sm text-gray-500">
            Qui a accès à la plateforme, et à quels domaines.
          </p>
        </div>
        <button
          onClick={() => setCreating(true)}
          className="rounded bg-gray-900 px-3 py-1.5 text-sm text-white"
        >
          Nouveau compte
        </button>
      </header>

      <div className="overflow-x-auto rounded border bg-white">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-gray-500">
              <th className="px-4 py-2 font-medium">Compte</th>
              <th className="px-4 py-2 font-medium">Rôle</th>
              <th className="px-4 py-2 font-medium">Domaines</th>
              <th className="px-4 py-2" />
            </tr>
          </thead>
          <tbody>
            {(users.data ?? []).map((u) => (
              <Row
                key={u.id}
                user={u}
                isSelf={u.email === me.data?.email}
                tenants={tenants.data ?? []}
              />
            ))}
            {users.isSuccess && users.data!.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-gray-500">
                  Aucun compte.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {creating && (
        <CreateDialog tenants={tenants.data ?? []} onClose={() => setCreating(false)} />
      )}
    </div>
  );
}

function Row({
  user,
  isSelf,
  tenants,
}: {
  user: User;
  isSelf: boolean;
  tenants: { id: string; domain: string }[];
}) {
  const update = useUpdateUser();
  const remove = useDeleteUser();
  const reset = useResetPassword();
  const [editing, setEditing] = useState(false);
  const [fiche, setFiche] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [newPassword, setNewPassword] = useState<string | null>(null);
  const admin = user.role === "platform_admin";

  async function resetPassword() {
    const pw = generatePassword();
    await reset.mutateAsync({ id: user.id, password: pw });
    setNewPassword(pw);
  }

  return (
    <>
      <tr className="border-t align-top">
        <td className="px-4 py-3">
          <div className="font-medium">{user.email}</div>
          {isSelf && <div className="text-xs text-gray-500">c'est vous</div>}
        </td>
        <td className="px-4 py-3">
          <span
            className={`rounded px-1.5 py-0.5 text-xs ${
              admin ? "bg-gray-900 text-white" : "bg-gray-100 text-gray-700"
            }`}
          >
            {admin ? "Administrateur" : "Lecture"}
          </span>
        </td>
        <td className="px-4 py-3">
          {admin ? (
            <span className="text-gray-500">tous</span>
          ) : (
            <div className="flex flex-wrap gap-1">
              {user.tenants.map((t) => (
                <span key={t.id} className="rounded bg-gray-100 px-1.5 py-0.5 text-xs">
                  {t.domain}
                </span>
              ))}
            </div>
          )}
        </td>
        <td className="px-4 py-3 text-right whitespace-nowrap">
          <button onClick={() => setFiche(true)} className="text-xs text-gray-600 hover:underline">
            Fiche
          </button>
          <span className="mx-2 text-gray-300">·</span>
          <button onClick={() => setEditing((e) => !e)} className="text-xs text-gray-600 hover:underline">
            Modifier
          </button>
          <span className="mx-2 text-gray-300">·</span>
          <button
            onClick={resetPassword}
            disabled={reset.isPending}
            className="text-xs text-gray-600 hover:underline"
          >
            Réinitialiser
          </button>
          <span className="mx-2 text-gray-300">·</span>
          {/* Un administrateur ne peut pas se supprimer : sans ce garde-fou, une fausse
              manœuvre le verrouille hors de sa propre plateforme. Le serveur le refuse
              aussi — le bouton grisé évite juste de le découvrir par une erreur. */}
          <button
            onClick={() => setConfirming(true)}
            disabled={isSelf}
            title={isSelf ? "Vous ne pouvez pas supprimer votre propre compte" : undefined}
            className="text-xs text-red-600 hover:underline disabled:cursor-not-allowed disabled:text-gray-300 disabled:no-underline"
          >
            Supprimer
          </button>
        </td>
      </tr>

      {fiche && (
        <tr>
          <td colSpan={4} className="p-0">
            <ProfileDialog
              mode="admin"
              userId={user.id}
              initial={toProfileValues(user)}
              onClose={() => setFiche(false)}
            />
          </td>
        </tr>
      )}

      {newPassword && (
        <tr className="border-t bg-amber-50">
          <td colSpan={4} className="px-4 py-3">
            <div className="text-sm">
              Nouveau mot de passe pour <strong>{user.email}</strong> — il ne sera plus jamais
              affiché :
            </div>
            <div className="mt-1 flex items-center gap-3">
              <code className="rounded border bg-white px-2 py-1 font-mono text-sm">
                {newPassword}
              </code>
              <button
                onClick={() => navigator.clipboard.writeText(newPassword)}
                className="text-xs text-gray-600 hover:underline"
              >
                Copier
              </button>
              <button
                onClick={() => setNewPassword(null)}
                className="text-xs text-gray-600 hover:underline"
              >
                J'ai noté
              </button>
            </div>
          </td>
        </tr>
      )}

      {editing && (
        <tr className="border-t bg-gray-50">
          <td colSpan={4} className="px-4 py-4">
            <EditForm
              user={user}
              isSelf={isSelf}
              tenants={tenants}
              onDone={() => setEditing(false)}
              onSave={(b) => update.mutateAsync({ id: user.id, ...b }).then(() => setEditing(false))}
            />
          </td>
        </tr>
      )}

      {confirming && (
        <tr className="border-t bg-red-50">
          <td colSpan={4} className="px-4 py-3 text-sm">
            Supprimer définitivement <strong>{user.email}</strong> ?
            <button
              onClick={() => remove.mutate(user.id, { onSettled: () => setConfirming(false) })}
              className="ml-3 rounded bg-red-600 px-2 py-1 text-xs text-white"
            >
              Supprimer
            </button>
            <button
              onClick={() => setConfirming(false)}
              className="ml-2 text-xs text-gray-600 hover:underline"
            >
              Annuler
            </button>
          </td>
        </tr>
      )}
    </>
  );
}

function EditForm({
  user,
  isSelf,
  tenants,
  onSave,
  onDone,
}: {
  user: User;
  isSelf: boolean;
  tenants: { id: string; domain: string }[];
  onSave: (b: { role: string; tenant_ids: string[] }) => Promise<unknown>;
  onDone: () => void;
}) {
  const [role, setRole] = useState(user.role);
  const [selected, setSelected] = useState<string[]>(user.tenants.map((t) => t.id));
  const [error, setError] = useState("");

  const needsTenant = role === "tenant_viewer" && selected.length === 0;

  async function save() {
    setError("");
    try {
      await onSave({ role, tenant_ids: selected });
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 409
          ? "Vous ne pouvez pas changer votre propre rôle."
          : "Enregistrement impossible.",
      );
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-4">
        <label className="text-sm">
          <span className="mr-2 text-gray-600">Rôle</span>
          <select
            value={role}
            disabled={isSelf}
            onChange={(e) => setRole(e.target.value as User["role"])}
            className="rounded border px-2 py-1 text-sm disabled:bg-gray-100 disabled:text-gray-400"
          >
            <option value="tenant_viewer">Lecture</option>
            <option value="platform_admin">Administrateur</option>
          </select>
        </label>
        {isSelf && (
          <span className="text-xs text-gray-500">
            Vous ne pouvez pas modifier votre propre rôle.
          </span>
        )}
      </div>

      {role === "tenant_viewer" && (
        <div>
          <div className="mb-1 text-xs uppercase tracking-wide text-gray-500">
            Domaines accessibles
          </div>
          <div className="flex flex-wrap gap-3">
            {tenants.map((t) => (
              <label key={t.id} className="flex items-center gap-1.5 text-sm">
                <input
                  type="checkbox"
                  checked={selected.includes(t.id)}
                  onChange={(e) =>
                    setSelected((s) =>
                      e.target.checked ? [...s, t.id] : s.filter((x) => x !== t.id),
                    )
                  }
                />
                {t.domain}
              </label>
            ))}
          </div>
          {needsTenant && (
            <p className="mt-1 text-xs text-amber-700">
              Un compte en lecture sans domaine ne verrait rien : sélectionnez-en au moins un.
            </p>
          )}
        </div>
      )}

      {error && <p className="text-sm text-red-600">{error}</p>}

      <div className="flex gap-2">
        <button
          onClick={save}
          disabled={needsTenant}
          className="rounded bg-gray-900 px-3 py-1.5 text-sm text-white disabled:opacity-40"
        >
          Enregistrer
        </button>
        <button onClick={onDone} className="rounded border px-3 py-1.5 text-sm">
          Annuler
        </button>
      </div>
    </div>
  );
}

function CreateDialog({
  tenants,
  onClose,
}: {
  tenants: { id: string; domain: string }[];
  onClose: () => void;
}) {
  const create = useCreateUser();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("tenant_viewer");
  const [selected, setSelected] = useState<string[]>([]);
  const [password] = useState(generatePassword);
  const [error, setError] = useState("");
  const [created, setCreated] = useState(false);

  const needsTenant = role === "tenant_viewer" && selected.length === 0;
  const ready = email.includes("@") && !needsTenant;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await create.mutateAsync({ email, role, password, tenant_ids: selected });
      setCreated(true);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 409
          ? "Ce compte existe déjà."
          : "Création impossible.",
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
        {created ? (
          <div className="space-y-4">
            <h2 className="font-semibold">Compte créé</h2>
            <p className="text-sm text-gray-600">
              Transmettez ces identifiants par un canal sûr. Le mot de passe ne sera plus
              jamais affiché.
            </p>
            <div className="rounded border bg-gray-50 p-3 font-mono text-sm">
              <div>{email}</div>
              <div>{password}</div>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => navigator.clipboard.writeText(`${email}\n${password}`)}
                className="flex-1 rounded border py-2 text-sm"
              >
                Copier
              </button>
              <button onClick={onClose} className="flex-1 rounded bg-gray-900 py-2 text-sm text-white">
                Fermer
              </button>
            </div>
          </div>
        ) : (
          <form onSubmit={submit} className="space-y-4">
            <h2 className="font-semibold">Nouveau compte</h2>

            <label className="block">
              <span className="text-xs text-gray-600">Adresse e-mail (identifiant)</span>
              <input
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="mt-1 w-full rounded border px-3 py-2 text-sm"
                placeholder="dmarc@client.com"
              />
            </label>

            <label className="block">
              <span className="text-xs text-gray-600">Rôle</span>
              <select
                value={role}
                onChange={(e) => setRole(e.target.value)}
                className="mt-1 w-full rounded border px-3 py-2 text-sm"
              >
                <option value="tenant_viewer">Lecture — un ou plusieurs domaines</option>
                <option value="platform_admin">Administrateur — tous les domaines</option>
              </select>
            </label>

            {role === "tenant_viewer" && (
              <div>
                <div className="mb-1 text-xs text-gray-600">Domaines accessibles</div>
                <div className="flex flex-wrap gap-3">
                  {tenants.map((t) => (
                    <label key={t.id} className="flex items-center gap-1.5 text-sm">
                      <input
                        type="checkbox"
                        checked={selected.includes(t.id)}
                        onChange={(e) =>
                          setSelected((s) =>
                            e.target.checked ? [...s, t.id] : s.filter((x) => x !== t.id),
                          )
                        }
                      />
                      {t.domain}
                    </label>
                  ))}
                </div>
              </div>
            )}

            <div className="rounded bg-gray-50 p-3 text-xs text-gray-600">
              Mot de passe généré : <code className="font-mono">{password}</code>
              <div className="mt-1">Il vous sera réaffiché une dernière fois après création.</div>
            </div>

            {error && <p className="text-sm text-red-600">{error}</p>}

            <div className="flex gap-2">
              <button type="button" onClick={onClose} className="flex-1 rounded border py-2 text-sm">
                Annuler
              </button>
              <button
                disabled={!ready || create.isPending}
                className="flex-1 rounded bg-gray-900 py-2 text-sm text-white disabled:opacity-40"
              >
                {create.isPending ? "…" : "Créer"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
