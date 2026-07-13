import { useState } from "react";

import {
  useAddRule,
  useDeleteRule,
  useRules,
  useTenants,
  useTestRules,
  useUpdateRule,
  type MatchingRule,
} from "../api/admin";
import { ApiError } from "../api/client";

/* La cascade évalue les types dans CET ordre et s'arrête au premier match. L'ordre
   n'est pas cosmétique : une règle `sender` court-circuite tout le reste. */
const CASCADE: {
  type: MatchingRule["rule_type"];
  label: string;
  help: string;
  danger?: string;
}[] = [
  {
    type: "sender",
    label: "Expéditeur",
    help: "L'adresse d'envoi contient ce texte. Évalué en premier, considéré comme certain.",
    danger:
      "Inadapté aux rapports DMARC : ils viennent TOUS de Google ou Microsoft, quel que soit le domaine concerné. Une telle règle attribuerait les rapports de tous vos clients à un seul domaine.",
  },
  {
    type: "subject_regex",
    label: "Sujet (expression régulière)",
    help: "C'est la règle utilisée pour DMARC : le domaine concerné n'apparaît que dans le sujet.",
  },
  {
    type: "keyword",
    label: "Mot-clé dans le sujet",
    help: "Le sujet contient ce texte. Moins précis qu'une expression régulière.",
  },
  {
    type: "alias",
    label: "Approchant (flou)",
    help: "Ressemblance approximative avec le sujet. Dernier recours, en dessous de 88 % de similarité rien n'est attribué.",
  },
];

const LABELS = Object.fromEntries(CASCADE.map((c) => [c.type, c.label]));

export function AdminRules() {
  const rules = useRules();
  const tenants = useTenants();
  const [adding, setAdding] = useState(false);

  const byType = CASCADE.map((c) => ({
    ...c,
    rules: (rules.data ?? []).filter((r) => r.rule_type === c.type),
  }));

  return (
    <div className="space-y-6 p-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Règles d'attribution</h1>
          <p className="max-w-3xl text-sm text-gray-500">
            Elles décident à quel domaine appartient chaque rapport reçu. Elles sont
            évaluées de haut en bas, et <strong>la première qui correspond l'emporte</strong> —
            une règle ne se lit donc jamais isolément.
          </p>
        </div>
        <button
          onClick={() => setAdding(true)}
          className="rounded bg-gray-900 px-3 py-1.5 text-sm text-white"
        >
          Ajouter une règle
        </button>
      </header>

      <Tester />

      <div className="space-y-4">
        {byType.map((group, i) => (
          <section key={group.type} className="rounded border bg-white">
            <div className="flex flex-wrap items-baseline gap-2 border-b px-4 py-3">
              <span className="font-mono text-xs text-gray-400">{i + 1}</span>
              <h2 className="text-sm font-medium">{group.label}</h2>
              <span className="text-xs text-gray-500">{group.help}</span>
            </div>

            {group.danger && group.rules.length > 0 && (
              <p className="border-b bg-red-50 px-4 py-2 text-xs text-red-800">
                <strong>Attention.</strong> {group.danger}
              </p>
            )}

            {group.rules.length === 0 ? (
              <p className="px-4 py-3 text-sm text-gray-400">Aucune règle.</p>
            ) : (
              <table className="w-full text-sm">
                <tbody>
                  {group.rules.map((r) => (
                    <RuleRow key={r.id} rule={r} />
                  ))}
                </tbody>
              </table>
            )}
          </section>
        ))}
      </div>

      {adding && <AddDialog tenants={tenants.data ?? []} onClose={() => setAdding(false)} />}
    </div>
  );
}

function RuleRow({ rule: r }: { rule: MatchingRule }) {
  const update = useUpdateRule();
  const remove = useDeleteRule();
  const [confirming, setConfirming] = useState(false);

  return (
    <tr className="border-t">
      <td className="w-40 px-4 py-2 font-mono text-xs">{r.domain}</td>
      <td className="px-4 py-2">
        <code
          className={`rounded bg-gray-50 px-1.5 py-0.5 text-xs ${
            r.is_active ? "" : "text-gray-400 line-through"
          }`}
        >
          {r.pattern}
        </code>
      </td>
      <td className="w-24 px-4 py-2 text-xs text-gray-500">priorité {r.priority}</td>
      <td className="w-56 whitespace-nowrap px-4 py-2 text-right">
        <button
          onClick={() => update.mutate({ id: r.id, is_active: !r.is_active })}
          className="text-xs text-gray-600 hover:underline"
        >
          {r.is_active ? "Désactiver" : "Activer"}
        </button>
        <span className="mx-2 text-gray-300">·</span>
        {confirming ? (
          <>
            <button
              onClick={() => remove.mutate(r.id)}
              className="rounded bg-red-600 px-2 py-0.5 text-xs text-white"
            >
              Confirmer
            </button>
            <button
              onClick={() => setConfirming(false)}
              className="ml-2 text-xs text-gray-600 hover:underline"
            >
              Annuler
            </button>
          </>
        ) : (
          <button
            onClick={() => setConfirming(true)}
            className="text-xs text-red-600 hover:underline"
          >
            Supprimer
          </button>
        )}
      </td>
    </tr>
  );
}

/* Le banc d'essai est la pièce maîtresse : il rejoue la cascade réelle sans rien
   écrire. C'est le seul moyen de vérifier une règle avant qu'elle ne range de vraies
   données — et de comprendre pourquoi un rapport part en quarantaine. */
function Tester() {
  const test = useTestRules();
  const [subject, setSubject] = useState(
    "Report domain: exemple.com Submitter: google.com Report-ID: 123",
  );
  const [from, setFrom] = useState("noreply-dmarc-support@google.com");
  const r = test.data;

  return (
    <section className="rounded border bg-white p-4">
      <h2 className="text-sm font-medium text-gray-700">Tester un message</h2>
      <p className="mb-3 text-xs text-gray-500">
        À quel domaine ce message serait-il attribué ? La cascade est rejouée à l'identique,
        sans rien enregistrer.
      </p>

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block">
          <span className="text-xs text-gray-600">Sujet</span>
          <input
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            className="mt-1 w-full rounded border px-3 py-2 font-mono text-xs"
          />
        </label>
        <label className="block">
          <span className="text-xs text-gray-600">Expéditeur</span>
          <input
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            className="mt-1 w-full rounded border px-3 py-2 font-mono text-xs"
          />
        </label>
      </div>

      <button
        onClick={() => test.mutate({ subject, from_address: from })}
        disabled={test.isPending}
        className="mt-3 rounded border px-3 py-1.5 text-sm hover:bg-gray-50 disabled:opacity-40"
      >
        {test.isPending ? "…" : "Tester"}
      </button>

      {r && (
        <div
          className={`mt-3 rounded border p-3 text-sm ${
            r.quarantined ? "border-amber-200 bg-amber-50" : "border-emerald-200 bg-emerald-50"
          }`}
        >
          {r.quarantined ? (
            <>
              <strong>Quarantaine.</strong> Aucune règle ne correspond — la plateforme refuse
              de deviner, le rapport resterait invisible de tous.
            </>
          ) : (
            <>
              Attribué à <strong className="font-mono">{r.domain}</strong> par la règle{" "}
              <em>{LABELS[r.method] ?? r.method}</em> (confiance{" "}
              {Math.round(r.confidence * 100)} %).
            </>
          )}
        </div>
      )}
    </section>
  );
}

function AddDialog({
  tenants,
  onClose,
}: {
  tenants: { id: string; domain: string }[];
  onClose: () => void;
}) {
  const add = useAddRule();
  const [tenantId, setTenantId] = useState("");
  const [type, setType] = useState<MatchingRule["rule_type"]>("subject_regex");
  const [pattern, setPattern] = useState("");
  const [priority, setPriority] = useState(100);
  const [error, setError] = useState("");

  const meta = CASCADE.find((c) => c.type === type)!;
  const ready = tenantId && pattern.trim();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await add.mutateAsync({ tenant_id: tenantId, rule_type: type, pattern, priority });
      onClose();
    } catch (err) {
      // Le serveur explique précisément pourquoi une règle est refusée (ex. un motif
      // qui capterait les rapports de tous les clients). On affiche son message.
      setError(err instanceof ApiError ? err.message : "Création impossible.");
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
        className="w-full max-w-lg space-y-4 rounded border bg-white p-6"
      >
        <h2 className="font-semibold">Ajouter une règle</h2>

        <label className="block">
          <span className="text-xs text-gray-600">Domaine attributaire</span>
          <select
            value={tenantId}
            onChange={(e) => setTenantId(e.target.value)}
            className="mt-1 w-full rounded border px-3 py-2 text-sm"
          >
            <option value="">— choisir —</option>
            {tenants.map((t) => (
              <option key={t.id} value={t.id}>
                {t.domain}
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="text-xs text-gray-600">Type</span>
          <select
            value={type}
            onChange={(e) => setType(e.target.value as MatchingRule["rule_type"])}
            className="mt-1 w-full rounded border px-3 py-2 text-sm"
          >
            {CASCADE.map((c) => (
              <option key={c.type} value={c.type}>
                {c.label}
              </option>
            ))}
          </select>
          <span className="mt-1 block text-xs text-gray-500">{meta.help}</span>
        </label>

        {meta.danger && (
          <p className="rounded border border-red-200 bg-red-50 p-3 text-xs text-red-800">
            <strong>Déconseillé.</strong> {meta.danger}
          </p>
        )}

        <label className="block">
          <span className="text-xs text-gray-600">Motif</span>
          <input
            value={pattern}
            onChange={(e) => setPattern(e.target.value)}
            placeholder={
              type === "subject_regex" ? "domain:\\s*exemple\\.com(?![\\w.-])" : "exemple.com"
            }
            className="mt-1 w-full rounded border px-3 py-2 font-mono text-xs"
          />
        </label>

        <label className="block w-32">
          <span className="text-xs text-gray-600">Priorité</span>
          <input
            type="number"
            min={1}
            max={1000}
            value={priority}
            onChange={(e) => setPriority(+e.target.value)}
            className="mt-1 w-full rounded border px-3 py-2 text-sm"
          />
          <span className="mt-1 block text-xs text-gray-500">
            La plus petite passe en premier, à type égal.
          </span>
        </label>

        {error && <p className="text-sm text-red-600">{error}</p>}

        <div className="flex gap-2">
          <button type="button" onClick={onClose} className="flex-1 rounded border py-2 text-sm">
            Annuler
          </button>
          <button
            disabled={!ready || add.isPending}
            className="flex-1 rounded bg-gray-900 py-2 text-sm text-white disabled:opacity-40"
          >
            {add.isPending ? "…" : "Ajouter"}
          </button>
        </div>
      </form>
    </div>
  );
}
