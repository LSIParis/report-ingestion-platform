import { type IpIntel, useIpIntel, useRefreshIpIntel } from "../api/ipIntel";

type Activity = IpIntel["activity"];

/** Cette IP a-t-elle des lignes TLS (kind="failure") à son actif ? `tls_sessions` ne
 *  vaut 0 que dans un seul cas : aucune ligne TLS pour cette IP. Un `null` (magnitude
 *  illisible) reste une activité TLS bien réelle — on ne le confond jamais avec 0. */
function aDeLActiviteTls(a: Activity): boolean {
  return a.tls_sessions !== 0 || Object.keys(a.tls_failures).length > 0;
}

/** Cette IP a-t-elle été évaluée comme expéditeur DMARC (au moins un message compté, ou
 *  un header_from observé) ? C'est la seule question qui rend un verdict SPF pertinent :
 *  SPF répond à « qui a le droit d'émettre au nom du domaine », pas « qui a pu nous
 *  livrer du courrier ». */
function vueCommeExpediteur(a: Activity): boolean {
  return a.messages > 0 || a.header_froms.length > 0;
}

/** IP vue UNIQUEMENT en TLS : elle nous a livré du courrier (ou tenté), elle n'a jamais
 *  prétendu émettre en notre nom. Un verdict SPF n'a alors aucun sens — l'appliquer
 *  reviendrait à accuser à tort un MTA de livraison honnête. */
function vueUniquementEnTls(a: Activity): boolean {
  return aDeLActiviteTls(a) && !vueCommeExpediteur(a);
}

/** Le verdict, en une phrase — c'est la seule chose que beaucoup liront.
 *
 * Identification et autorisation sont INDÉPENDANTES : « SendGrid, mais votre SPF ne
 * l'autorise pas » est parfaitement cohérent, et c'est même le cas le plus utile.
 * Les confondre serait l'erreur à ne pas commettre. */
function verdict(d: IpIntel): { titre: string; ton: "danger" | "warn" | "ok" } {
  const autorise = d.spf.result === "pass";
  const nom = d.sender?.name;

  // Voir vueUniquementEnTls() : sur une IP qui ne nous a jamais servi d'expéditeur, le
  // verdict qui compte est le TLS, pas le SPF. Priorité sur toute la logique SPF
  // ci-dessous, qu'on ne réécrit pas mais qu'on ne laisse pas parler à tort.
  if (vueUniquementEnTls(d.activity)) {
    return {
      titre: nom
        ? `${nom} — échecs de session TLS vers votre domaine (SPF non applicable)`
        : "MTA de livraison — échecs de session TLS vers votre domaine (SPF non applicable)",
      ton: "danger",
    };
  }

  // « Aucun SPF publié » n'est PAS « le SPF refuse cette IP ». Les deux mènent au même
  // échec, mais pas au même geste : dans un cas on ajoute un mécanisme, dans l'autre on
  // n'a tout simplement pas d'enregistrement SPF. Le dire de travers enverrait
  // l'exploitant corriger ce qui n'existe pas.
  if (d.spf.result === "none") {
    return {
      titre: nom
        ? `${nom} — mais aucun SPF n'est publié sur votre domaine`
        : "Aucun SPF n'est publié sur votre domaine : rien n'autorise cette IP",
      ton: "danger",
    };
  }

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
              {vueUniquementEnTls(data.activity) && (
                <span className="ml-1 text-xs text-gray-500">
                  — non applicable : cette IP ne vous a jamais envoyé de courrier en
                  votre nom, elle vous a livré du courrier (ou tenté) via TLS.
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
            {aDeLActiviteTls(data.activity) && (
              <>
                <Fait label="Sessions TLS en échec">
                  {formatTlsSessions(data.activity)}
                </Fait>
                {Object.keys(data.activity.tls_failures).length > 0 && (
                  <Fait label="Types d'échec">
                    {Object.entries(data.activity.tls_failures)
                      .map(([type, v]) => `${type} : ${formatTlsCount(v)}`)
                      .join(" · ")}
                  </Fait>
                )}
              </>
            )}
          </Section>

          <Section titre="Que faire">
            {aDeLActiviteTls(data.activity) && (
              // Sur une IP en enforce, « il n'y a rien à faire » serait faux : le
              // courrier de ce MTA serait purement et simplement refusé. On le dit,
              // qu'un expéditeur connu corresponde ou non.
              <p className="text-sm leading-relaxed mb-2">
                Ce serveur n'arrive pas à établir de session TLS vérifiée vers votre
                domaine. En mode appliqué (MTA-STS <code className="font-mono">enforce</code>),
                son courrier serait <strong>refusé</strong>, sans alerte de votre côté.
                Avant de durcir, comprenez pourquoi : certificat expiré, non couvrant, ou
                nom de serveur MX qui ne correspond pas à la politique publiée.
              </p>
            )}
            {vueUniquementEnTls(data.activity) ? (
              // `sender.remediation` est TOUJOURS une instruction SPF/DKIM (« Ajoutez
              // include:... à votre SPF »). Sur une IP vue UNIQUEMENT en TLS, ce
              // conseil n'a aucun sens : elle ne prétend pas émettre en votre nom, le
              // paragraphe ci-dessus l'a déjà dit. L'afficher pousserait à élargir un
              // SPF sans aucune raison, trois lignes après avoir dit que le SPF ne
              // s'applique pas. Le conseil qui compte ici porte sur le chiffrement
              // (déjà donné plus haut) : rien à ajouter dans cette section.
              null
            ) : data.sender ? (
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

/* sessions === null : magnitude inconnue — jamais affiché comme "0", ce serait rassurant
   et faux. partial === true : le nombre est un MINORANT, le vrai total peut être plus
   élevé. Même convention que MtaStsPanel.tsx (formatFailureSessions). */
function formatTlsCount(v: { sessions: number | null; partial: boolean }): string {
  if (v.sessions === null) return "nombre inconnu";
  const n = v.sessions.toLocaleString("fr-FR");
  return v.partial ? `au moins ${n}` : n;
}

function formatTlsSessions(a: Activity): string {
  return formatTlsCount({ sessions: a.tls_sessions, partial: a.tls_partial });
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
