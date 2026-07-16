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
import { MtaStsPanel } from "../components/MtaStsPanel";
import { OnboardingPanel } from "../components/OnboardingPanel";

export function Domains() {
  const domains = useDomains();
  const requeue = useRequeueQuarantine();
  const [creating, setCreating] = useState(false);
  const [procedure, setProcedure] = useState<string | null>(null);
  const [tls, setTls] = useState<Domain | null>(null);
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
              <Row
                key={d.id}
                domain={d}
                onProcedure={() => setProcedure(d.id)}
                onTls={() => setTls(d)}
              />
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

      {creating && (
        <CreateDialog
          onClose={() => setCreating(false)}
          onCreated={(id) => {
            setCreating(false);
            setProcedure(id); // on enchaîne directement sur la procédure : la créer ne
          }}                  // sert à rien tant que le DNS n'est pas posé.
        />
      )}
      {procedure && (
        <OnboardingPanel tenantId={procedure} onClose={() => setProcedure(null)} />
      )}
      {tls && (
        <MtaStsPanel tenantId={tls.id} domain={tls.domain} onClose={() => setTls(null)} />
      )}
    </div>
  );
}

function Row({
  domain: d,
  onProcedure,
  onTls,
}: {
  domain: Domain;
  onProcedure: () => void;
  onTls: () => void;
}) {
  const update = useUpdateDomain();
  const remove = useDeleteDomain();
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  const [alerting, setAlerting] = useState(false);
  const [emails, setEmails] = useState(d.alert_email ?? "");
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
          <button onClick={onProcedure} className="text-xs text-gray-900 hover:underline">
            Procédure
          </button>
          <span className="mx-2 text-gray-300">·</span>
          <button onClick={onTls} className="text-xs text-gray-600 hover:underline">
            Chiffrement
          </button>
          <span className="mx-2 text-gray-300">·</span>
          <button onClick={() => setAlerting((a) => !a)} className="text-xs text-gray-600 hover:underline">
            Alertes
          </button>
          <span className="mx-2 text-gray-300">·</span>
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

      {alerting && (
        <tr className="border-t bg-gray-50">
          <td colSpan={5} className="px-4 py-3">
            <label className="block text-sm">
              <span className="text-xs text-gray-600">
                Destinataire(s) des alertes e-mail — adresses séparées par des virgules
                (vide = aucune)
              </span>
              <input
                value={emails}
                onChange={(e) => setEmails(e.target.value)}
                placeholder="ops@client.fr, secu@client.fr"
                className="mt-1 w-full rounded border px-3 py-2 text-sm"
              />
            </label>
            <div className="mt-2 flex gap-2">
              <button
                onClick={() =>
                  update.mutate(
                    { id: d.id, alert_email: emails },
                    { onSuccess: () => setAlerting(false) },
                  )
                }
                className="rounded bg-gray-900 px-3 py-1.5 text-sm text-white"
              >
                Enregistrer
              </button>
              <button onClick={() => setAlerting(false)} className="rounded border px-3 py-1.5 text-sm">
                Annuler
              </button>
            </div>
          </td>
        </tr>
      )}

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

/* La création ne fait qu'inscrire le domaine dans la base : tant que le DNS n'est pas
   posé, aucun rapport n'arrivera. On enchaîne donc directement sur la procédure, plutôt
   que d'afficher un « créé ! » qui laisserait croire que c'est terminé. */
function CreateDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const create = useCreateDomain();
  const [domain, setDomain] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");

  const clean = domain.trim().toLowerCase().replace(/^@/, "");
  const ready = /^[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(clean);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const created = await create.mutateAsync({
        domain: clean,
        name: name.trim() || undefined,
      });
      onCreated(created.id);
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
      <form
        onSubmit={submit}
        onMouseDown={(e) => e.stopPropagation()}
        className="w-full max-w-md space-y-4 rounded border bg-white p-6"
      >
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
          La procédure DNS complète — six enregistrements sur deux zones — s'affichera
          juste après, et se vérifiera toute seule.
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
    </div>
  );
}
