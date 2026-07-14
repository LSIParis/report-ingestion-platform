import { useOnboarding, type Step } from "../api/domains";

/* La procédure de mise en conformité d'un domaine, vérifiée en direct.

   Elle vit dans l'application plutôt que dans un document : six enregistrements DNS
   répartis sur deux zones, plus une politique à servir. Aucune de ces erreurs ne
   produit d'alerte — elles se traduisent seulement par des rapports qui n'arrivent
   jamais. La seule défense utile est une liste qui se vérifie elle-même. */

const TONE: Record<Step["status"], { chip: string; label: string; row: string }> = {
  ok: { chip: "bg-emerald-100 text-emerald-800", label: "Fait", row: "" },
  todo: { chip: "bg-gray-200 text-gray-700", label: "À faire", row: "" },
  warn: { chip: "bg-red-100 text-red-800", label: "Incorrect", row: "bg-red-50/40" },
  unknown: { chip: "bg-gray-100 text-gray-500", label: "?", row: "" },
};

export function OnboardingPanel({
  tenantId,
  onClose,
}: {
  tenantId: string;
  onClose: () => void;
}) {
  const q = useOnboarding(tenantId);
  const d = q.data;

  const done = d?.steps.filter((s) => s.status === "ok").length ?? 0;
  const total = d?.steps.length ?? 0;
  const wrong = d?.steps.filter((s) => s.status === "warn") ?? [];

  return (
    <div
      className="fixed inset-0 z-30 flex items-start justify-center overflow-y-auto bg-black/30 p-4"
      onMouseDown={onClose}
    >
      <div
        className="my-8 w-full max-w-3xl space-y-4 rounded border bg-white p-6"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="font-semibold">
              Mise en conformité de <span className="font-mono">{d?.domain ?? "…"}</span>
            </h2>
            <p className="text-sm text-gray-500">
              Vérifié en direct sur le DNS. Rien ici ne produit d'erreur visible si c'est
              oublié — seulement des rapports qui n'arrivent jamais.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => q.refetch()}
              disabled={q.isFetching}
              className="rounded border px-3 py-1.5 text-sm hover:bg-gray-50 disabled:opacity-40"
            >
              {q.isFetching ? "Vérification…" : "Revérifier"}
            </button>
            <button onClick={onClose} className="rounded border px-3 py-1.5 text-sm">
              Fermer
            </button>
          </div>
        </header>

        {d && (
          <>
            <div className="flex flex-wrap items-center gap-3 rounded border bg-gray-50 p-3 text-sm">
              <span className="font-medium">
                {done} / {total} étapes faites
              </span>
              {wrong.length > 0 && (
                <span className="rounded bg-red-100 px-2 py-0.5 text-xs text-red-800">
                  {wrong.length} enregistrement(s) publié(s) mais incorrect(s)
                </span>
              )}
              {d.mx.length > 0 && (
                <span className="ml-auto text-xs text-gray-500">
                  MX détecté : <code className="font-mono">{d.mx.join(", ")}</code>
                </span>
              )}
            </div>

            {/* Le mx: de la politique MTA-STS doit correspondre au CERTIFICAT du MX.
                On le déduit du MX réel plutôt que de laisser deviner : s'y tromper, en
                mode enforce, fait perdre du courrier. */}
            {d.mx_policy.length > 0 && (
              <p className="text-xs text-gray-500">
                Politique MTA-STS à utiliser pour ce domaine :{" "}
                <code className="rounded bg-gray-100 px-1 py-0.5 font-mono">
                  mx: {d.mx_policy.join(" · mx: ")}
                </code>
              </p>
            )}

            <ol className="space-y-3">
              {d.steps.map((s, i) => (
                <StepCard key={s.key} step={s} index={i + 1} />
              ))}
            </ol>
          </>
        )}

        {q.isError && (
          <p className="text-sm text-red-600">
            Vérification impossible. Le serveur n'a pas pu interroger le DNS.
          </p>
        )}
      </div>
    </div>
  );
}

function StepCard({ step: s, index }: { step: Step; index: number }) {
  const tone = TONE[s.status];
  return (
    <li className={`rounded border p-4 ${tone.row}`}>
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="font-mono text-xs text-gray-400">{index}</span>
        <h3 className="text-sm font-medium">{s.title}</h3>
        <span className={`rounded px-1.5 py-0.5 text-xs ${tone.chip}`}>{tone.label}</span>
        <span className="ml-auto text-xs text-gray-400">
          {s.zone === "plateforme" ? "sur la plateforme" : `zone ${s.zone}`}
        </span>
      </div>

      <p className="mt-1 text-xs text-gray-600">{s.why}</p>

      {s.detail && <p className="mt-1 text-xs font-medium text-red-700">{s.detail}</p>}

      {s.record && s.status !== "ok" && <RecordCard record={s.record} />}

      {s.found && (
        <p className="mt-2 break-all text-xs text-gray-500">
          Publié actuellement : <code className="font-mono">{s.found}</code>
        </p>
      )}
    </li>
  );
}

function RecordCard({ record }: { record: { type: string; name: string; value: string } }) {
  return (
    <div className="mt-2 rounded border bg-white">
      <div className="flex items-center justify-between border-b bg-gray-50 px-3 py-1.5">
        <span className="text-xs text-gray-500">Enregistrement à créer</span>
        <button
          onClick={() => navigator.clipboard.writeText(record.value)}
          className="text-xs text-gray-600 hover:underline"
        >
          Copier la valeur
        </button>
      </div>
      <dl className="divide-y text-xs">
        {[
          ["Type", record.type],
          ["Nom", record.name],
          ["Valeur", record.value],
        ].map(([k, v]) => (
          <div key={k} className="grid grid-cols-[4rem_1fr]">
            <dt className="bg-gray-50 px-3 py-1.5 uppercase tracking-wide text-gray-500">
              {k}
            </dt>
            <dd className="overflow-x-auto whitespace-nowrap px-3 py-1.5 font-mono">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
