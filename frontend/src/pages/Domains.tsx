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

type StatusFilter = "all" | "active" | "waiting" | "suspended";

// L'état affiché d'un domaine, dérivé comme dans <Row> : suspendu (non actif),
// « en attente » (actif mais aucun rapport reçu), ou actif.
function domainState(d: Domain): Exclude<StatusFilter, "all"> {
  if (d.status !== "active") return "suspended";
  return d.reports === 0 ? "waiting" : "active";
}

export function Domains() {
  const domains = useDomains();
  const requeue = useRequeueQuarantine();
  const [creating, setCreating] = useState(false);
  const [procedure, setProcedure] = useState<string | null>(null);
  const [tls, setTls] = useState<Domain | null>(null);
  const [requeued, setRequeued] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");

  async function runRequeue() {
    const r = await requeue.mutateAsync();
    setRequeued(r.requeued);
  }

  const all = domains.data ?? [];
  const q = query.trim().toLowerCase();
  const filtered = all.filter((d) => {
    if (statusFilter !== "all" && domainState(d) !== statusFilter) return false;
    if (!q) return true;
    return d.domain.toLowerCase().includes(q) || d.name.toLowerCase().includes(q);
  });
  const filtering = q !== "" || statusFilter !== "all";

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

      {/* Filtre client sur la liste déjà chargée : recherche (domaine ou nom du
          client) + puces d'état. Aucun appel réseau. */}
      {all.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Rechercher un domaine ou un client…"
            className="w-64 rounded border px-3 py-1.5 text-sm"
          />
          <div className="flex gap-1">
            {(
              [
                ["all", "Tous"],
                ["active", "Actifs"],
                ["waiting", "En attente"],
                ["suspended", "Suspendus"],
              ] as [StatusFilter, string][]
            ).map(([value, label]) => (
              <button
                key={value}
                onClick={() => setStatusFilter(value)}
                className={`rounded-full px-3 py-1 text-xs ${
                  statusFilter === value
                    ? "bg-gray-900 text-white"
                    : "border text-gray-600 hover:bg-gray-50"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          {filtering && (
            <span className="text-xs text-gray-500">
              {filtered.length} / {all.length}
            </span>
          )}
        </div>
      )}

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
            {filtered.map((d) => (
              <Row
                key={d.id}
                domain={d}
                onProcedure={() => setProcedure(d.id)}
                onTls={() => setTls(d)}
              />
            ))}
            {domains.isSuccess && all.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-gray-500">
                  Aucun domaine surveillé.
                </td>
              </tr>
            )}
            {domains.isSuccess && all.length > 0 && filtered.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-gray-500">
                  Aucun domaine ne correspond au filtre.
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

const DOMAIN_RE = /^[a-z0-9-]+(\.[a-z0-9-]+)+$/;

// Une saisie libre (une zone de texte) → une liste de domaines nettoyés. On accepte
// tout séparateur courant (retour ligne, virgule, point-virgule, espace), on met en
// minuscules, on retire un « @ » de tête, et on dédoublonne en gardant l'ordre.
function parseDomains(text: string): { valid: string[]; invalid: string[] } {
  const seen = new Set<string>();
  const valid: string[] = [];
  const invalid: string[] = [];
  for (const raw of text.split(/[\s,;]+/)) {
    const clean = raw.trim().toLowerCase().replace(/^@/, "");
    if (!clean || seen.has(clean)) continue;
    seen.add(clean);
    (DOMAIN_RE.test(clean) ? valid : invalid).push(clean);
  }
  return { valid, invalid };
}

type CreateOutcome = { domain: string; status: "created" | "exists" | "error"; id?: string };

/* La création ne fait qu'inscrire le domaine dans la base : tant que le DNS n'est pas
   posé, aucun rapport n'arrivera. Pour un domaine unique on enchaîne donc directement
   sur la procédure ; pour un ajout multiple on affiche un récapitulatif, chaque ligne
   du tableau gardant son bouton « Procédure ». */
function CreateDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const create = useCreateDomain();
  const [text, setText] = useState("");
  const [name, setName] = useState("");
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<CreateOutcome[] | null>(null);

  const { valid, invalid } = parseDomains(text);
  const single = valid.length === 1;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!valid.length || running) return;
    setRunning(true);
    const outcomes: CreateOutcome[] = [];
    for (const domain of valid) {
      try {
        const created = await create.mutateAsync({
          domain,
          // Le nom ne s'applique qu'à un ajout unitaire (une seule ligne).
          name: single ? name.trim() || undefined : undefined,
        });
        outcomes.push({ domain, status: "created", id: created.id });
      } catch (err) {
        outcomes.push({
          domain,
          status: err instanceof ApiError && err.status === 409 ? "exists" : "error",
        });
      }
    }
    setRunning(false);

    // Cas unitaire réussi : on garde l'enchaînement historique vers la procédure DNS.
    const created = outcomes.filter((o) => o.status === "created");
    if (valid.length === 1 && invalid.length === 0 && created.length === 1) {
      onCreated(created[0].id!);
      return;
    }
    setResults(outcomes);
  }

  if (results) {
    const createdCount = results.filter((o) => o.status === "created").length;
    return (
      <Shell onClose={onClose}>
        <h2 className="font-semibold">Ajout terminé</h2>
        <p className="text-sm text-gray-600">
          {createdCount} domaine(s) créé(s) sur {results.length} traité(s).
          {createdCount > 0 &&
            " Ouvrez la procédure DNS de chacun depuis le tableau (bouton « Procédure »)."}
        </p>
        <ul className="max-h-64 space-y-1 overflow-y-auto text-sm">
          {results.map((o) => (
            <li key={o.domain} className="flex items-center justify-between gap-3">
              <span className="font-mono">{o.domain}</span>
              <OutcomeBadge status={o.status} />
            </li>
          ))}
          {invalid.map((d) => (
            <li key={d} className="flex items-center justify-between gap-3">
              <span className="font-mono">{d}</span>
              <OutcomeBadge status="invalid" />
            </li>
          ))}
        </ul>
        <div className="flex justify-end">
          <button onClick={onClose} className="rounded bg-gray-900 px-3 py-1.5 text-sm text-white">
            Fermer
          </button>
        </div>
      </Shell>
    );
  }

  return (
    <Shell onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <h2 className="font-semibold">Ajouter un ou plusieurs domaines</h2>

        <label className="block">
          <span className="text-xs text-gray-600">
            Domaines à surveiller — un par ligne (ou séparés par des virgules)
          </span>
          <textarea
            autoFocus
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={5}
            placeholder={"exemple.com\nautre-client.fr"}
            className="mt-1 w-full rounded border px-3 py-2 font-mono text-sm"
          />
          <span className="mt-1 block text-xs text-gray-500">
            {valid.length} domaine(s) valide(s)
            {invalid.length > 0 && (
              <span className="text-amber-700"> · {invalid.length} ignoré(s) : {invalid.join(", ")}</span>
            )}
          </span>
        </label>

        {single && (
          <label className="block">
            <span className="text-xs text-gray-600">Nom du client (facultatif)</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Exemple SA"
              className="mt-1 w-full rounded border px-3 py-2 text-sm"
            />
          </label>
        )}

        <p className="rounded bg-gray-50 p-3 text-xs text-gray-600">
          La règle qui reconnaît les rapports de chaque domaine est créée automatiquement.
          La procédure DNS complète — six enregistrements sur deux zones — reste à faire pour
          chacun, et se vérifie toute seule.
        </p>

        <div className="flex gap-2">
          <button type="button" onClick={onClose} className="flex-1 rounded border py-2 text-sm">
            Annuler
          </button>
          <button
            disabled={!valid.length || running}
            className="flex-1 rounded bg-gray-900 py-2 text-sm text-white disabled:opacity-40"
          >
            {running ? "…" : valid.length > 1 ? `Créer ${valid.length} domaines` : "Créer"}
          </button>
        </div>
      </form>
    </Shell>
  );
}

function Shell({ onClose, children }: { onClose: () => void; children: React.ReactNode }) {
  return (
    <div
      className="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-4"
      onMouseDown={onClose}
    >
      <div
        onMouseDown={(e) => e.stopPropagation()}
        className="w-full max-w-md space-y-4 rounded border bg-white p-6"
      >
        {children}
      </div>
    </div>
  );
}

function OutcomeBadge({ status }: { status: CreateOutcome["status"] | "invalid" }) {
  const map = {
    created: ["bg-emerald-100 text-emerald-800", "créé"],
    exists: ["bg-gray-200 text-gray-700", "déjà surveillé"],
    error: ["bg-red-100 text-red-800", "échec"],
    invalid: ["bg-amber-100 text-amber-800", "invalide"],
  } as const;
  const [cls, label] = map[status];
  return <span className={`rounded px-1.5 py-0.5 text-xs ${cls}`}>{label}</span>;
}
