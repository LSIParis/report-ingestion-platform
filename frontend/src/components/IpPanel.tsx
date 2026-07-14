import { type IpIntel, useIpIntel, useRefreshIpIntel } from "../api/ipIntel";

/** Le verdict, en une phrase — c'est la seule chose que beaucoup liront.
 *
 * Identification et autorisation sont INDÉPENDANTES : « SendGrid, mais votre SPF ne
 * l'autorise pas » est parfaitement cohérent, et c'est même le cas le plus utile.
 * Les confondre serait l'erreur à ne pas commettre. */
function verdict(d: IpIntel): { titre: string; ton: "danger" | "warn" | "ok" } {
  const autorise = d.spf.result === "pass";
  const nom = d.sender?.name;

  if (nom && autorise) return { titre: `${nom} — autorisée par votre SPF`, ton: "ok" };
  if (nom) return { titre: `${nom} — mais votre SPF ne l'autorise pas`, ton: "warn" };
  if (autorise) return { titre: "Expéditeur non identifié, mais autorisé par votre SPF", ton: "warn" };
  if (d.spf.result === "indetermine" || d.spf.result === "permerror")
    return { titre: "Expéditeur non identifié — SPF non évaluable", ton: "warn" };
  return {
    titre: "Expéditeur non identifié, autorisé par aucun mécanisme de votre SPF",
    ton: "danger",
  };
}

const TONS = {
  danger: "bg-red-50 border-red-200 text-red-900",
  warn: "bg-amber-50 border-amber-200 text-amber-900",
  ok: "bg-green-50 border-green-200 text-green-900",
};

const SPF_LABEL: Record<IpIntel["spf"]["result"], string> = {
  pass: "autorisée",
  fail: "refusée",
  softfail: "refusée (souple)",
  neutral: "neutre",
  none: "aucun SPF publié",
  permerror: "SPF en erreur",
  indetermine: "indéterminé",
};

export function IpPanel({ ip, onClose }: { ip: string; onClose: () => void }) {
  const { data, isLoading, error } = useIpIntel(ip);
  const refresh = useRefreshIpIntel();

  return (
    <aside className="fixed right-0 top-0 h-full w-[26rem] bg-white border-l shadow-xl
                      overflow-y-auto p-5 z-20">
      <div className="flex items-start justify-between mb-4">
        <h2 className="font-mono text-lg">{ip}</h2>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-700 text-xl">×</button>
      </div>

      {isLoading && <p className="text-gray-500">Interrogation du DNS…</p>}
      {error && <p className="text-gray-500">Cette IP n'apparaît dans aucun de vos rapports.</p>}

      {data && (
        <>
          <div className={`border rounded p-3 mb-5 ${TONS[verdict(data).ton]}`}>
            <p className="font-medium">{verdict(data).titre}</p>
          </div>

          <Section titre="Ce que dit le DNS">
            <Fait label="Reverse DNS">
              {data.ptr ? (
                <>
                  <span className="font-mono text-xs">{data.ptr}</span>
                  {data.fcrdns ? (
                    <span className="ml-2 text-xs text-green-700">✓ vérifié</span>
                  ) : (
                    // Un PTR non vérifié n'est pas un détail : c'est ce qui empêche
                    // d'identifier l'expéditeur, et c'est un signal en soi.
                    <span className="ml-2 text-xs text-red-700">✗ incohérent</span>
                  )}
                </>
              ) : (
                <span className="text-gray-500">aucun — les routeurs légitimes en ont tous un</span>
              )}
            </Fait>
            <Fait label="Réseau">
              {data.asn ? (
                <>
                  AS{data.asn} · {data.as_org ?? "?"}
                  {data.country ? ` · ${data.country}` : ""}
                </>
              ) : (
                <span className="text-gray-500">inconnu</span>
              )}
            </Fait>
            <Fait label="Votre SPF">
              {SPF_LABEL[data.spf.result]}
              {data.spf.mechanism && (
                <span className="ml-1 font-mono text-xs text-gray-500">
                  ({data.spf.mechanism})
                </span>
              )}
            </Fait>
            {data.hosted_by && (
              <Fait label="Hébergement">
                {/* « Hébergé chez » situe, ne conclut pas : un ASN ne nomme pas
                    l'expéditeur — la plupart des IP d'AWS sont des EC2 quelconques. */}
                hébergé chez {data.hosted_by}
              </Fait>
            )}
          </Section>

          <Section titre="Ce que vous avez observé">
            <Fait label="Messages">{data.activity.messages}</Fait>
            <Fait label="Période">
              {data.activity.first_seen ?? "?"} → {data.activity.last_seen ?? "?"}
            </Fait>
            <Fait label="Alignement">
              {Object.entries(data.activity.aligned)
                .map(([k, v]) => `${k} : ${v}`)
                .join(" · ") || "—"}
            </Fait>
            <Fait label="Traitement">
              {Object.entries(data.activity.dispositions)
                .map(([k, v]) => `${k} : ${v}`)
                .join(" · ") || "—"}
            </Fait>
            <Fait label="Domaines usurpés">
              {data.activity.header_froms.join(", ") || "—"}
            </Fait>
          </Section>

          <Section titre="Que faire">
            {data.sender ? (
              <p className="text-sm leading-relaxed">{data.sender.remediation}</p>
            ) : (
              <p className="text-sm leading-relaxed text-gray-700">
                Aucun expéditeur connu ne correspond, et votre SPF ne l'autorise pas. Si
                vous ne reconnaissez pas ce service, il n'y a rien à faire : c'est une
                usurpation, et votre politique DMARC la traite déjà. Ne l'autorisez que si
                vous identifiez un service qui vous appartient.
              </p>
            )}
          </Section>

          <button
            onClick={() => refresh.mutate(ip)}
            disabled={refresh.isPending}
            className="mt-2 text-xs text-blue-600 hover:underline disabled:opacity-40"
          >
            {refresh.isPending ? "…" : "Réinterroger le DNS"}
          </button>
          <p className="mt-1 text-xs text-gray-400">
            Faits DNS relevés le {new Date(data.checked_at).toLocaleString("fr-FR")}
          </p>
        </>
      )}
    </aside>
  );
}

function Section({ titre, children }: { titre: string; children: React.ReactNode }) {
  return (
    <section className="mb-5">
      <h3 className="text-xs uppercase tracking-wide text-gray-400 mb-2">{titre}</h3>
      <dl className="space-y-1">{children}</dl>
    </section>
  );
}

function Fait({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2 text-sm">
      <dt className="w-32 shrink-0 text-gray-500">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}
