import { useState } from "react";

import { type Alert, useAlerts } from "../api/alerts";

/* Les trois natures d'alerte, dites en français d'exploitant — pas en jargon de table.
   Le libellé doit dire QUOI FAIRE, pas décrire une ligne de base de données. */
const NATURES: Record<Alert["kind"], { titre: string; quoi: string }> = {
  never_reported: {
    titre: "Aucun rapport, jamais",
    quoi: "Ce domaine n'a jamais rien reçu depuis son ajout. Son enregistrement DMARC n'a probablement jamais été publié — le client se croit protégé et ne l'est pas.",
  },
  domain_silent: {
    titre: "Les rapports ont cessé",
    quoi: "Ce domaine recevait des rapports et n'en reçoit plus. Enregistrement DMARC supprimé ou modifié, changement d'hébergeur : on ne sait plus rien de ce domaine.",
  },
  tls_failure: {
    titre: "Échec de chiffrement",
    quoi: "Des expéditeurs n'arrivent pas à établir une connexion chiffrée vérifiée vers ce domaine.",
  },
};

export function Alerts() {
  const [status, setStatus] = useState<"open" | "all">("open");
  const { data, isPending, isError } = useAlerts(status);

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Alertes</h1>
          <p className="text-sm text-gray-500">
            Une alerte s'ouvre quand la condition devient vraie et se ferme d'elle-même
            quand elle disparaît. Elle n'est jamais renvoyée deux fois.
          </p>
        </div>
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value as "open" | "all")}
          className="rounded border px-3 py-1 text-sm"
        >
          <option value="open">Ouvertes</option>
          <option value="all">Toutes (avec les résolues)</option>
        </select>
      </div>

      {isPending && <p className="text-gray-500">Chargement…</p>}
      {isError && (
        <p className="rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          Impossible de charger les alertes. Cette liste est peut-être incomplète — ne
          concluez pas qu'il n'y a rien à signaler.
        </p>
      )}

      {data && data.length === 0 && (
        <p className="text-gray-500">
          {status === "open" ? "Aucune alerte ouverte." : "Aucune alerte."}
        </p>
      )}

      <ul className="space-y-3">
        {data?.map((a) => <Ligne key={a.id} a={a} />)}
      </ul>
    </div>
  );
}

function Ligne({ a }: { a: Alert }) {
  const nature = NATURES[a.kind];
  const resolue = a.closed_at !== null;
  const ton = resolue
    ? "border-gray-200 bg-gray-50"
    : a.severity === "critical"
      ? "border-red-300 bg-red-50"
      : "border-amber-300 bg-amber-50";

  return (
    <li className={`rounded border p-4 ${ton}`}>
      <div className="flex items-baseline justify-between gap-4">
        <div>
          <span className="font-mono text-sm">{a.domain}</span>
          <span className="ml-3 font-medium">{nature?.titre ?? a.kind}</span>
          {resolue && (
            <span className="ml-2 text-xs text-gray-500">· résolue</span>
          )}
        </div>
        <span className="shrink-0 text-xs text-gray-500">
          depuis le {new Date(a.opened_at).toLocaleDateString("fr-FR")}
        </span>
      </div>

      <p className="mt-1 text-sm text-gray-700">{nature?.quoi}</p>

      {Object.keys(a.payload).length > 0 && (
        <dl className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-xs text-gray-600">
          {Object.entries(a.payload).map(([k, v]) => (
            <div key={k} className="flex gap-1">
              <dt className="text-gray-400">{k}</dt>
              <dd className="font-mono">{String(v)}</dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  );
}
