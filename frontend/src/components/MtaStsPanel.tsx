import { useEffect, useState } from "react";

import { ApiError } from "../api/client";
import { type TlsFailure, type TlsPosture, useMtaSts, useSaveMtaSts, useTlsPosture } from "../api/domains";

/* MTA-STS force les serveurs distants à chiffrer le courrier qu'ils envoient vers ce
   domaine, et à vérifier le certificat du MX.

   C'est le SEUL réglage de la plateforme qui peut faire perdre du courrier : en mode
   enforce, un expéditeur qui ne trouve pas le MX du domaine dans la politique refuse de
   livrer. Rien ne casse de notre côté — les expéditeurs renoncent, chacun de son côté,
   sans alerte. D'où la mise en garde explicite avant de durcir. */

const MODES = [
  {
    v: "none",
    label: "Aucune politique",
    help: "Rien n'est servi. Les expéditeurs chiffrent au mieux, sans garantie.",
  },
  {
    v: "testing",
    label: "Observation",
    help: "Les expéditeurs SIGNALENT les échecs de chiffrement (rapports TLS) sans jamais bloquer le courrier. C'est ici qu'on commence.",
  },
  {
    v: "enforce",
    label: "Appliqué",
    help: "Les expéditeurs REFUSENT de livrer si le chiffrement ne valide pas. Ne passez ici qu'après une à deux semaines d'observation sans échec.",
  },
] as const;

export function MtaStsPanel({
  tenantId,
  domain,
  onClose,
}: {
  tenantId: string;
  domain: string;
  onClose: () => void;
}) {
  const q = useMtaSts(tenantId);
  const save = useSaveMtaSts(tenantId);
  const tls = useTlsPosture(tenantId);
  const [mode, setMode] = useState<string>("");
  const [maxAge, setMaxAge] = useState(86400);
  const [mx, setMx] = useState("");
  const [error, setError] = useState("");
  const [confirmEnforce, setConfirmEnforce] = useState(false);

  useEffect(() => {
    if (!q.data) return;
    setMode(q.data.mode);
    setMaxAge(q.data.max_age);
    setMx(q.data.mx.join("\n"));
  }, [q.data]);

  const detected = q.data?.detected_mx ?? [];
  const current = mx.split("\n").map((s) => s.trim()).filter(Boolean);
  const matchesDns =
    detected.length > 0 &&
    JSON.stringify([...current].sort()) === JSON.stringify([...detected].sort());

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (mode === "enforce" && !confirmEnforce) {
      setConfirmEnforce(true);
      return;
    }
    try {
      await save.mutateAsync({ mode, max_age: maxAge, mx: current });
      setConfirmEnforce(false);
      onClose();
    } catch (err) {
      // Le serveur refuse un enforce qui couperait la réception, et explique pourquoi.
      setError(err instanceof ApiError ? err.message : "Enregistrement impossible.");
      setConfirmEnforce(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-30 flex items-start justify-center overflow-y-auto bg-black/30 p-4"
      onMouseDown={onClose}
    >
      <form
        onSubmit={submit}
        onMouseDown={(e) => e.stopPropagation()}
        className="my-8 w-full max-w-2xl space-y-4 rounded border bg-white p-6"
      >
        <header>
          <h2 className="font-semibold">
            Chiffrement du courrier entrant — <span className="font-mono">{domain}</span>
          </h2>
          <p className="text-sm text-gray-500">
            MTA-STS oblige les serveurs distants à chiffrer et à vérifier le certificat de
            votre serveur de messagerie. Sans lui, un attaquant peut retirer le chiffrement
            de la négociation et lire le courrier en clair.
          </p>
        </header>

        {tls.isLoading ? (
          <div className="rounded border border-gray-300 bg-gray-50 p-3 text-xs text-gray-700">
            Vérification des rapports TLS en cours…
          </div>
        ) : tls.isError ? (
          <div className="rounded border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900">
            <strong>La posture TLS n'a pas pu être vérifiée.</strong> On ne sait donc rien
            de l'état du chiffrement pour ce domaine — ce n'est en aucun cas le signe que
            tout va bien. Réessayez avant d'envisager le mode appliqué.
          </div>
        ) : (
          tls.data && <TlsVerdict p={tls.data} />
        )}

        <fieldset className="space-y-2">
          <legend className="text-xs uppercase tracking-wide text-gray-500">Mode</legend>
          {MODES.map((m) => (
            <label
              key={m.v}
              className={`flex gap-3 rounded border p-3 ${
                mode === m.v ? "border-gray-900 bg-gray-50" : "hover:bg-gray-50"
              }`}
            >
              <input
                type="radio"
                name="mode"
                className="mt-1"
                checked={mode === m.v}
                onChange={() => {
                  setMode(m.v);
                  setConfirmEnforce(false);
                }}
              />
              <span>
                <span className="block text-sm font-medium">{m.label}</span>
                <span className="block text-xs text-gray-600">{m.help}</span>
              </span>
            </label>
          ))}
        </fieldset>

        <label className="block">
          <span className="text-xs uppercase tracking-wide text-gray-500">
            Serveurs de messagerie autorisés (un par ligne)
          </span>
          <textarea
            value={mx}
            onChange={(e) => setMx(e.target.value)}
            rows={3}
            className="mt-1 w-full rounded border px-3 py-2 font-mono text-xs"
          />
          <span className="mt-1 block text-xs text-gray-500">
            Doit correspondre au <strong>certificat</strong> présenté par le serveur, pas
            seulement à son nom.
          </span>
        </label>

        {detected.length > 0 && (
          <div
            className={`rounded border p-3 text-xs ${
              matchesDns
                ? "border-emerald-200 bg-emerald-50 text-emerald-900"
                : "border-amber-300 bg-amber-50 text-amber-900"
            }`}
          >
            {matchesDns ? (
              <>Conforme au MX réellement publié dans le DNS.</>
            ) : (
              <>
                <strong>Écart avec le DNS.</strong> Le MX réel de ce domaine est{" "}
                <code className="font-mono">{detected.join(", ")}</code>. En mode appliqué,
                une politique qui ne le couvre pas fait <strong>refuser tout le courrier
                entrant</strong>.
              </>
            )}
          </div>
        )}

        <label className="block w-64">
          <span className="text-xs uppercase tracking-wide text-gray-500">
            Durée de mise en cache
          </span>
          <select
            value={maxAge}
            onChange={(e) => setMaxAge(+e.target.value)}
            className="mt-1 w-full rounded border px-3 py-2 text-sm"
          >
            <option value={86400}>1 jour — pendant l'observation</option>
            <option value={604800}>7 jours — une fois la politique éprouvée</option>
            <option value={2592000}>30 jours — protection maximale</option>
          </select>
          <span className="mt-1 block text-xs text-gray-500">
            Une politique en cache ne peut pas être raccourcie rétroactivement : en cas
            d'erreur, elle reste active chez les expéditeurs pendant toute cette durée.
          </span>
        </label>

        {q.data?.preview && (
          <div>
            <span className="text-xs uppercase tracking-wide text-gray-500">
              Politique actuellement servie
            </span>
            <pre className="mt-1 overflow-x-auto rounded border bg-gray-50 p-3 font-mono text-xs">
              {q.data.preview}
            </pre>
            <p className="mt-1 text-xs text-gray-500">
              Publiée sur <code className="font-mono">mta-sts.{domain}</code> · id{" "}
              <code className="font-mono">{q.data.policy_id}</code>
            </p>
          </div>
        )}

        {confirmEnforce && (
          <div className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-900">
            <strong>Confirmer le mode appliqué ?</strong> À partir de maintenant, un
            expéditeur qui ne parvient pas à établir une connexion chiffrée vérifiée vers{" "}
            <code className="font-mono">{current.join(", ") || "—"}</code>{" "}
            <strong>cessera de livrer le courrier</strong>. Ne le faites qu'après avoir
            observé les rapports TLS sans aucun échec.
            <div className="mt-2 text-xs">
              Cliquez à nouveau sur Enregistrer pour confirmer.
            </div>
          </div>
        )}

        {error && <p className="text-sm text-red-600">{error}</p>}

        <div className="flex gap-2">
          <button type="button" onClick={onClose} className="flex-1 rounded border py-2 text-sm">
            Annuler
          </button>
          <button
            disabled={save.isPending}
            className={`flex-1 rounded py-2 text-sm text-white disabled:opacity-40 ${
              confirmEnforce ? "bg-red-600" : "bg-gray-900"
            }`}
          >
            {save.isPending ? "…" : confirmEnforce ? "Confirmer" : "Enregistrer"}
          </button>
        </div>

        <p className="text-xs text-gray-500">
          Après chaque modification, l'identifiant de la politique change : la procédure du
          domaine vous rappellera de mettre à jour l'enregistrement DNS correspondant.
        </p>
      </form>
    </div>
  );
}

/* Le verdict TLS se lit JUSTE AVANT le sélecteur de mode, parce que c'est exactement là
   que se prend la décision qu'il éclaire. Une page séparée qu'il faut penser à ouvrir ne
   servirait personne.

   Trois états, et le premier est le plus important : ne RIEN savoir n'est pas rassurant.
   Un domaine silencieux n'est pas un domaine sans échec — c'est un domaine sur lequel on
   n'a aucune donnée. Le dire autrement ferait durcir à l'aveugle, ce que TLS-RPT sert
   précisément à éviter. */
function TlsVerdict({ p }: { p: TlsPosture }) {
  if (p.sessions_total === 0) {
    return (
      <div className="rounded border border-gray-300 bg-gray-50 p-3 text-xs text-gray-700">
        <strong>Aucun rapport TLS reçu sur {p.days} jours.</strong> On ne sait donc pas si
        le chiffrement fonctionne — ce n'est pas la même chose que « tout va bien ».
        Publiez l'enregistrement <code className="font-mono">_smtp._tls</code> (voir la
        procédure du domaine) avant de durcir, sinon vous durcirez à l'aveugle.
      </div>
    );
  }

  if (p.safe_to_enforce) {
    return (
      <div className="rounded border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-900">
        <strong>
          {p.sessions_ok.toLocaleString("fr-FR")} sessions sur {p.days} jours, toutes
          chiffrées, aucun échec.
        </strong>{" "}
        Le passage en mode appliqué est sûr.
        {p.reporters.length > 0 && (
          <span className="block mt-1 text-emerald-800">
            D'après : {p.reporters.join(", ")}.
          </span>
        )}
      </div>
    );
  }

  // Des échecs, ou des données incomplètes (un compteur manquant dans un rapport) : dans
  // les deux cas, le mode appliqué refuserait potentiellement du courrier légitime. On dit
  // les deux séparément, sinon un exploitant qui ne voit aucun échec ne comprendra pas
  // pourquoi le feu vert lui est refusé.
  // La phrase de clôture diffère selon la branche : quand il y a des échecs connus, on peut
  // parler de « ces messages » sans mentir. Quand il n'y a QUE de l'incomplétude, aucun
  // message en échec n'est identifié — on ne peut affirmer que de l'incertitude, pas un fait.
  return (
    <div className="rounded border border-red-300 bg-red-50 p-3 text-xs text-red-900">
      {p.sessions_failed > 0 ? (
        <>
          <strong>
            {p.sessions_failed.toLocaleString("fr-FR")} session
            {plural(p.sessions_failed)} en échec de chiffrement sur {p.days} jours
          </strong>{" "}
          (sur {p.sessions_total.toLocaleString("fr-FR")} sessions rapportées). En mode
          appliqué, ces messages seraient <strong>refusés</strong>. Corrigez d'abord.
        </>
      ) : (
        <>
          <strong>Aucun échec visible, mais des données incomplètes sur {p.days} jours.</strong>{" "}
          (sur {p.sessions_total.toLocaleString("fr-FR")} sessions rapportées). Impossible
          de garantir qu'aucun message ne serait refusé en mode appliqué : les rapports
          reçus sont incomplets.
        </>
      )}
      {p.incomplete_rows > 0 && (
        <p className="mt-2">
          <strong>
            {p.incomplete_rows} ligne{plural(p.incomplete_rows)} de résumé
            incomplète{plural(p.incomplete_rows)}
          </strong>{" "}
          : un fournisseur a rapporté un résultat sans indiquer combien de sessions il
          couvrait. Le nombre réel d'échecs peut donc être supérieur à ce qui est affiché
          ici — on ne peut pas garantir l'exhaustivité.
        </p>
      )}
      {p.failures.length > 0 && (
        <ul className="mt-2 space-y-1">
          {p.failures.map((f, i) => (
            <li key={i} className="font-mono">
              {f.result_type} · {formatFailureSessions(f)}
              {f.sending_mta_ip && <> · depuis {f.sending_mta_ip}</>}
              {f.receiving_mx_hostname && <> · vers {f.receiving_mx_hostname}</>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Accord du pluriel français, factorisé pour éviter la répétition de `n > 1 ? "s" : ""`.
function plural(n: number): string {
  return n > 1 ? "s" : "";
}

/* sessions === null : la magnitude est inconnue (aucune occurrence lisible dans le
   rapport) — on ne l'affiche JAMAIS comme « 0 session », ce serait rassurant et faux.
   partial === true : le nombre est un MINORANT, le vrai total peut être plus élevé. */
function formatFailureSessions(f: TlsFailure): string {
  if (f.sessions === null) return "nombre de sessions inconnu";
  const n = f.sessions.toLocaleString("fr-FR");
  const suffix = plural(f.sessions);
  return f.partial ? `au moins ${n} session${suffix}` : `${n} session${suffix}`;
}
