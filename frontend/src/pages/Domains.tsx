import { useState } from "react";

import { ApiError } from "../api/client";
import {
  useCreateDomain,
  useDeleteDomain,
  useDomains,
  useRequeueQuarantine,
  useUpdateDomain,
  type Domain,
} from "../api/domains";

const COLLECTION_MAILBOX = "dmarc.lsi@lsiparis.tech";

export function Domains() {
  const domains = useDomains();
  const requeue = useRequeueQuarantine();
  const [creating, setCreating] = useState(false);
  const [requeued, setRequeued] = useState<number | null>(null);

  async function runRequeue() {
    const r = await requeue.mutateAsync();
    setRequeued(r.requeued);
  }

  return (
    <div className="space-y-6 p-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Domaines surveillés</h1>
          <p className="text-sm text-gray-500">
            Un domaine devient visible ici dès qu'il est créé ; ses rapports arrivent
            24 à 48 h après la publication de son enregistrement DMARC.
          </p>
        </div>
        <button
          onClick={() => setCreating(true)}
          className="rounded bg-gray-900 px-3 py-1.5 text-sm text-white"
        >
          Ajouter un domaine
        </button>
      </header>

      <div className="overflow-x-auto rounded border bg-white">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-gray-500">
              <th className="px-4 py-2 font-medium">Domaine</th>
              <th className="px-4 py-2 font-medium">État</th>
              <th className="px-4 py-2 text-right font-medium">Rapports</th>
              <th className="px-4 py-2 font-medium">Dernier rapport</th>
              <th className="px-4 py-2" />
            </tr>
          </thead>
          <tbody>
            {(domains.data ?? []).map((d) => (
              <Row key={d.id} domain={d} />
            ))}
            {domains.isSuccess && domains.data!.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-gray-500">
                  Aucun domaine surveillé.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* La quarantaine est le cas courant : le client publie son DMARC avant que le
          domaine n'existe ici, et ses rapports s'accumulent sans destinataire. */}
      <section className="rounded border bg-white p-4">
        <h2 className="text-sm font-medium text-gray-700">Rapports en attente d'attribution</h2>
        <p className="mt-1 max-w-2xl text-sm text-gray-500">
          Les rapports arrivés avant la création de leur domaine sont mis en quarantaine :
          la plateforme refuse de deviner à qui ils appartiennent. Après avoir ajouté un
          domaine, rejouez-les pour les rattacher. Rien n'est perdu — l'e-mail d'origine est
          conservé.
        </p>
        <div className="mt-3 flex items-center gap-3">
          <button
            onClick={runRequeue}
            disabled={requeue.isPending}
            className="rounded border px-3 py-1.5 text-sm hover:bg-gray-50 disabled:opacity-40"
          >
            {requeue.isPending ? "En cours…" : "Rejouer la quarantaine"}
          </button>
          {requeued !== null && (
            <span className="text-sm text-gray-600">
              {requeued === 0
                ? "Aucun rapport en attente."
                : `${requeued} rapport(s) remis en file. Le traitement prend quelques secondes.`}
            </span>
          )}
        </div>
      </section>

      {creating && <CreateDialog onClose={() => setCreating(false)} />}
    </div>
  );
}

function Row({ domain: d }: { domain: Domain }) {
  const update = useUpdateDomain();
  const remove = useDeleteDomain();
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  const suspended = d.status !== "active";
  const silent = d.reports === 0;

  async function del() {
    setError("");
    try {
      await remove.mutateAsync(d.id);
    } catch (e) {
      // 409 : le domaine a de l'historique. Le message du serveur explique pourquoi
      // et vers quoi se rabattre — on l'affiche tel quel plutôt qu'un « erreur ».
      setError(e instanceof ApiError ? e.message : "Suppression impossible.");
      setConfirming(false);
    }
  }

  return (
    <>
      <tr className="border-t align-top">
        <td className="px-4 py-3">
          <div className="font-mono">{d.domain}</div>
          {d.name !== d.domain && <div className="text-xs text-gray-500">{d.name}</div>}
        </td>
        <td className="px-4 py-3">
          {suspended ? (
            <span className="rounded bg-gray-200 px-1.5 py-0.5 text-xs text-gray-700">
              Suspendu
            </span>
          ) : silent ? (
            <span
              className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800"
              title="Aucun rapport reçu : l'enregistrement DMARC est peut-être absent, ou le domaine n'envoie pas de courrier."
            >
              En attente
            </span>
          ) : (
            <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-xs text-emerald-800">
              Actif
            </span>
          )}
        </td>
        <td className="px-4 py-3 text-right tabular-nums">{d.reports}</td>
        <td className="px-4 py-3 text-gray-500">
          {d.last_report_at ? new Date(d.last_report_at).toLocaleDateString("fr-FR") : "—"}
        </td>
        <td className="whitespace-nowrap px-4 py-3 text-right">
          <button
            onClick={() => update.mutate({ id: d.id, active: suspended })}
            className="text-xs text-gray-600 hover:underline"
          >
            {suspended ? "Réactiver" : "Suspendre"}
          </button>
          <span className="mx-2 text-gray-300">·</span>
          <button
            onClick={() => setConfirming(true)}
            className="text-xs text-red-600 hover:underline"
          >
            Supprimer
          </button>
        </td>
      </tr>

      {confirming && (
        <tr className="border-t bg-red-50">
          <td colSpan={5} className="px-4 py-3 text-sm">
            Supprimer <strong>{d.domain}</strong> ? Possible uniquement s'il n'a jamais rien
            collecté.
            <button onClick={del} className="ml-3 rounded bg-red-600 px-2 py-1 text-xs text-white">
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

      {error && (
        <tr className="border-t bg-amber-50">
          <td colSpan={5} className="px-4 py-3 text-sm text-amber-900">
            {error}
            <button
              onClick={() => setError("")}
              className="ml-3 text-xs text-gray-600 hover:underline"
            >
              Fermer
            </button>
          </td>
        </tr>
      )}
    </>
  );
}

function CreateDialog({ onClose }: { onClose: () => void }) {
  const create = useCreateDomain();
  const [domain, setDomain] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [created, setCreated] = useState(false);

  const clean = domain.trim().toLowerCase().replace(/^@/, "");
  const ready = /^[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(clean);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await create.mutateAsync({ domain: clean, name: name.trim() || undefined });
      setCreated(true);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 409
          ? "Ce domaine est déjà surveillé."
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
        className="w-full max-w-lg rounded border bg-white p-6"
        onMouseDown={(e) => e.stopPropagation()}
      >
        {created ? (
          <div className="space-y-4">
            <h2 className="font-semibold">
              <span className="font-mono">{clean}</span> est créé
            </h2>
            <p className="text-sm text-gray-600">
              La plateforme est prête à recevoir ses rapports. Il reste{" "}
              <strong>deux enregistrements DNS</strong> à publier — sans eux, aucun rapport
              n'arrivera jamais.
            </p>

            <Record
              title="1. Chez le client, dans sa zone DNS"
              rows={[
                ["Type", "TXT"],
                ["Nom", "_dmarc"],
                ["Valeur", `v=DMARC1; p=none; rua=mailto:${COLLECTION_MAILBOX}; fo=1; adkim=s;`],
              ]}
              note="Commencez toujours en p=none : un p=reject sur un domaine jamais audité fait disparaître du courrier légitime."
            />
            <Record
              title="2. Dans la zone lsiparis.tech"
              rows={[
                ["Type", "TXT"],
                ["Nom", `${clean}._report._dmarc`],
                ["Valeur", "v=DMARC1"],
              ]}
              note="Autorise notre domaine à recevoir les rapports du sien (RFC 7489 §7.1)."
            />

            <button
              onClick={onClose}
              className="w-full rounded bg-gray-900 py-2 text-sm text-white"
            >
              Terminé
            </button>
          </div>
        ) : (
          <form onSubmit={submit} className="space-y-4">
            <h2 className="font-semibold">Ajouter un domaine</h2>

            <label className="block">
              <span className="text-xs text-gray-600">Domaine à surveiller</span>
              <input
                autoFocus
                value={domain}
                onChange={(e) => setDomain(e.target.value)}
                placeholder="exemple.com"
                className="mt-1 w-full rounded border px-3 py-2 font-mono text-sm"
              />
              {domain && !ready && (
                <span className="mt-1 block text-xs text-amber-700">
                  Attendu : un nom de domaine (exemple.com), pas une adresse e-mail.
                </span>
              )}
            </label>

            <label className="block">
              <span className="text-xs text-gray-600">Nom du client (facultatif)</span>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Exemple SA"
                className="mt-1 w-full rounded border px-3 py-2 text-sm"
              />
            </label>

            <p className="rounded bg-gray-50 p-3 text-xs text-gray-600">
              La règle qui reconnaît les rapports de ce domaine est créée automatiquement.
              Les enregistrements DNS à publier vous seront affichés juste après.
            </p>

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

function Record({
  title,
  rows,
  note,
}: {
  title: string;
  rows: [string, string][];
  note: string;
}) {
  const value = rows[rows.length - 1][1];
  return (
    <div className="rounded border">
      <div className="flex items-center justify-between border-b bg-gray-50 px-3 py-2">
        <span className="text-xs font-medium">{title}</span>
        <button
          onClick={() => navigator.clipboard.writeText(value)}
          className="text-xs text-gray-600 hover:underline"
        >
          Copier la valeur
        </button>
      </div>
      <dl className="divide-y text-xs">
        {rows.map(([k, v]) => (
          <div key={k} className="grid grid-cols-[5rem_1fr]">
            <dt className="bg-gray-50 px-3 py-1.5 uppercase tracking-wide text-gray-500">{k}</dt>
            <dd className="overflow-x-auto whitespace-nowrap px-3 py-1.5 font-mono">{v}</dd>
          </div>
        ))}
      </dl>
      <p className="border-t px-3 py-2 text-xs text-gray-500">{note}</p>
    </div>
  );
}
