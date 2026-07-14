# Enrichissement des IP sources d'un rapport DMARC

**Date** : 2026-07-14
**Statut** : validé, prêt pour le plan d'implémentation

## Le problème

Sur la page d'un rapport, une IP source rejetée s'affiche comme une chaîne nue. Elle ne
répond à aucune des questions qu'on se pose en la lisant, et il n'y en a qu'une qui
compte :

> Est-ce un service légitime que j'ai oublié d'autoriser (CRM, imprimante, routeur
> d'e-mailing, prestataire) — ou quelqu'un qui usurpe mon domaine ?

De cette réponse dépend tout le reste : dans un cas on corrige SPF/DKIM, dans l'autre on
ne fait rien et on avance vers `p=reject`. Tant qu'on ne sait pas trancher, le domaine
reste bloqué en `p=none` — c'est-à-dire sans protection.

Pire, la table actuelle affiche les colonnes brutes du JSONB (`Object.keys` de la
première ligne). `auth_spf` et `auth_dkim` — que le parsing extrait **déjà** et qui
identifient souvent l'expéditeur à eux seuls — y sont noyés, illisibles.

## Ce qu'on construit

Un clic sur une IP source ouvre un panneau latéral qui **commence par un verdict** :

> **Expéditeur non identifié — aucun mécanisme de votre SPF ne l'autorise.**
> 412 messages, tous en échec d'alignement, aucune signature DKIM.

ou

> **SendGrid — autorisée par votre SPF** (`include:sendgrid.net`).

Suivi des éléments qui fondent ce verdict, et d'une conclusion actionnable.

## Périmètre

**Inclus** — quatre signaux obtenus **en DNS**, sans API tierce, sans clé, sans qu'aucune
donnée de nos clients ne sorte de la plateforme, plus un catalogue local :

1. **PTR + FCrDNS** — le nom inverse, et sa validation aller-retour.
2. **ASN, organisation, pays** — via le service DNS public de Team Cymru.
3. **Couverture SPF** — cette IP est-elle autorisée par le SPF du domaine surveillé ?
4. **Activité de l'IP pour ce tenant** — volume, période, dispositions, alignement,
   domaines vus dans `auth_spf` / `auth_dkim`.
5. **Catalogue d'expéditeurs connus** — voir plus bas. Sa valeur n'est pas le nom, c'est
   **la remédiation**.

**Exclu, délibérément** :

- *Listes noires (DNSBL)*. Leur usage via un résolveur public est interdit au-delà d'un
  faible volume ; Spamhaus impose une clé DQS. Dépendance externe payante pour un signal
  moins décisif que la couverture SPF.
- *Géolocalisation fine*. Le pays vient déjà de Team Cymru ; aller plus loin imposerait
  une base MaxMind à tenir à jour, pour un gain nul sur la décision.
- *Page « Sources » agrégée par IP* (toutes les IP du domaine triées par volume rejeté).
  Elle répond à une autre question — « quelles sont mes IP à problème ? » — et se
  justifiera. Pas maintenant.
- *Enrichissement à l'ingestion.* L'enrichissement est **à la demande** : rien ne
  ralentit le pipeline, il ne dépend pas du DNS pour aboutir, et l'historique déjà en
  base en bénéficie immédiatement, sans rejeu.

## Architecture

### `app/services/ip_intel.py` — les signaux

Chaque signal est **indépendant et dégradable** : un timeout, un NXDOMAIN, une réponse
illisible valent « inconnu ». Aucun ne casse le panneau, aucun ne fait échouer la
requête. Budget DNS total borné (~4 s) ; les résolutions se font via `dnspython`, déjà
dépendance du projet (`resolver.lifetime` court, comme dans `services/onboarding.py`).

**`ptr(ip) -> {hostname, fcrdns}`**
Résolution inverse, puis **revérification du nom en A/AAAA** : si l'IP n'y figure pas, le
PTR est signalé *incohérent*. Un PTR seul se falsifie ; l'aller-retour, non.

**`asn(ip) -> {asn, prefix, country, org}`**
Team Cymru, en DNS pur :
- IPv4 : octets inversés + `.origin.asn.cymru.com`, TXT
- IPv6 : nibbles inversés + `.origin6.asn.cymru.com`, TXT
- puis `AS<n>.asn.cymru.com`, TXT → nom de l'organisation

On interroge une IP qui est **déjà publique** : ce n'est pas une fuite, c'est une
question sur Internet posée à Internet.

**`spf_covers(domain, ip) -> {result, mechanism}`** — voir ci-dessous.

### L'évaluateur SPF — honnête plutôt que malin

Il déplie le SPF du domaine surveillé et teste l'appartenance de l'IP.

- Mécanismes gérés : `ip4`, `ip6`, `include`, `redirect`, `a`, `mx`, `all`, avec leurs
  qualificateurs (`+ - ~ ?`).
- **Limite normative de 10 requêtes DNS** (RFC 7208 §4.6.4), appliquée strictement.
  C'est la conformité *et* la protection : une chaîne `include:` hostile ne peut pas nous
  faire tourner en rond.
- Ce qu'il ne sait pas évaluer — macros `%{…}`, `ptr`, `exists` — ne produit **jamais une
  réponse fausse** : il renvoie **`indéterminé`**, et le dit à l'écran. On ne devine
  jamais (invariant maison, cf. CLAUDE.md §6).

Résultat : `pass` | `fail` | `softfail` | `neutral` | `none` | `indéterminé`, **et le
mécanisme qui a tranché**. C'est le mécanisme, pas le verdict, qui rend l'écran
actionnable : « autorisée par `include:spf.protection.outlook.com` » dit à l'exploitant
que le rejet vient de l'alignement, pas de l'autorisation — deux corrections opposées.

Le SPF n'est **pas** mis en cache avec l'IP : il dépend du couple (domaine, IP) et
l'enregistrement du client change. Il est recalculé à chaque consultation.

### Le catalogue d'expéditeurs — `backend/senders/<clé>.json`

Même convention que `profiles/` : **un fichier, aucun code, aucun déploiement.** Chargés
au démarrage (le catalogue est petit), rechargés à chaud n'est pas nécessaire.

```json
{
  "name": "SendGrid",
  "ptr_suffixes": [".sendgrid.net"],
  "asn": [11377, 396507],
  "spf_include": "sendgrid.net",
  "remediation": "Ajoutez `include:sendgrid.net` à votre SPF, puis activez la signature
                  DKIM (Sender Authentication) dans la console SendGrid."
}
```

**Le piège, et la règle qui l'évite.** Un catalogue naïf ne se contente pas d'être
inutile : il fabrique des affirmations fausses, et rassurantes. AS16509 est Amazon — mais
l'écrasante majorité de ses IP sont des EC2 quelconques, pas Amazon SES : une entrée
« AS16509 → Amazon SES » étiquetterait « Amazon SES » une VM louée par un usurpateur.
AS15169 couvre Gmail, Google Workspace *et* des VM GCP. D'où :

1. **Le suffixe PTR est la seule clé qui nomme** — et **uniquement si le FCrDNS est
   vérifié**. Sans l'aller-retour, n'importe qui pose un PTR menteur et se fait passer
   pour SendGrid. Un PTR incohérent qui matche le catalogue → **pas d'identification**,
   et l'incohérence est affichée comme un signal en soi.
2. **Une correspondance par ASN seul ne nomme jamais l'expéditeur.** Elle dit
   « *hébergé chez* Amazon » — ce qui n'autorise rien et ne rassure de rien. Elle sert à
   situer, pas à conclure.
3. **Le catalogue ne contredit jamais les faits DNS.** Il se pose par-dessus ; il
   n'écrase ni la couverture SPF, ni le FCrDNS. Un expéditeur reconnu **et** non couvert
   par le SPF reste un échec — c'est même le cas le plus utile : « SendGrid, mais votre
   SPF ne l'autorise pas », avec la remédiation exacte à appliquer.
4. **Une entrée absente ne bloque rien.** Le catalogue est un bonus ; sans lui, le
   panneau affiche les faits DNS bruts, comme s'il n'existait pas.

Le contenu initial se limite aux routeurs qu'on rencontre réellement (Microsoft 365,
Google Workspace, SendGrid, Brevo, Mailjet, Amazon SES, Mailchimp) — via leurs suffixes
PTR, pas leurs ASN.

### `ip_intel` — le cache, et l'écart assumé à l'invariant n°1

```
ip_intel(ip inet PRIMARY KEY, ptr text, fcrdns bool, asn int,
         as_org text, country text, checked_at timestamptz)
```

**Sans `tenant_id`.** Ce sont des faits publics sur Internet, pas des données de client —
au même titre que `tenant` ou `audit_log`, déjà rangées par `0002_rls.py` dans les
« tables non-tenant, GRANT explicites, pas de RLS ».

L'écart à l'invariant n°1 est **signalé**, et voici ce qui le compense : **l'API ne lit
jamais cette table directement.** La route vérifie d'abord, **sous RLS**, que l'IP
demandée apparaît dans une ligne de rapport visible du tenant courant. Une IP que ce
tenant n'a jamais vue → **404**, quel que soit son contenu dans le cache. On ne peut donc
pas sonder l'existence d'une IP chez un autre client : le canal de fuite est fermé
**avant** d'atteindre le cache.

Fraîcheur : `checked_at` de plus de 7 jours → réinterrogé. Bouton « réinterroger »
explicite dans le panneau.

**Le cache ne stocke pas l'expéditeur reconnu**, seulement les faits DNS. L'appariement
avec le catalogue se fait à la lecture. Conséquence voulue : corriger une entrée du
catalogue, ou en ajouter une, prend effet **immédiatement** sur tout l'historique — sans
purge de cache, sans rejeu. Une erreur de catalogue reste réparable par un fichier.

Index nécessaire à la vérification d'appartenance et au résumé d'activité :

```sql
CREATE INDEX ix_report_row_source_ip ON report_row (tenant_id, (data->>'source_ip'));
```

Migration : `0004_ip_intel`.

### `app/api/ip_intel.py` — la route

`GET /ip-intel/{ip}` (route tenant, session déjà scopée par `get_db` — **aucun
`WHERE tenant_id` applicatif**) :

1. Vérifie l'appartenance sous RLS : existe-t-il une `report_row` du tenant avec
   `data->>'source_ip' = :ip` ? Sinon **404**.
2. Renvoie l'enrichissement (cache si frais, sinon interrogation DNS puis mise en cache).
3. Renvoie le **résumé d'activité de cette IP pour ce tenant** : volume, période,
   dispositions, alignement, domaines vus dans `auth_spf` / `auth_dkim`.

`POST /ip-intel/{ip}/refresh` : même contrôle d'appartenance, force la réinterrogation.

C'est souvent le résumé qui tranche : « 412 messages, 100 % en échec, tous sur votre
`header_from`, aucune signature DKIM » ne se lit pas comme « 3 messages ».

### L'écran

**`ReportDetail`** : quand les lignes portent une clé `source_ip` — détection par les
données, pas par un nom de profil : `Report` ne stocke pas le format, seulement
`source_type` (`attachment`/`body`) et `profile_id` — la table générique cède la place à
un rendu DMARC : **IP source cliquable**, volume, disposition en badge, alignement, et
`auth_spf` / `auth_dkim` enfin lisibles. Les autres rapports gardent la table actuelle.

**`components/IpPanel.tsx`** : panneau latéral, dans cet ordre — le verdict d'abord, les
preuves ensuite, l'action pour finir :

1. **Verdict** : expéditeur identifié ou non (catalogue, sur PTR vérifié), autorisé par
   le SPF ou non. Les deux sont indépendants : « SendGrid, mais votre SPF ne l'autorise
   pas » est un verdict parfaitement cohérent — et le plus utile de tous.
2. **Preuves** : PTR (+ badge *vérifié* / *incohérent*), ASN + organisation + pays,
   mécanisme SPF, activité observée.
3. **Conclusion actionnable** : la `remediation` du catalogue si l'expéditeur est
   reconnu — sinon, ignorer : usurpation probable.

## Erreurs et dégradation

| Situation | Comportement |
|---|---|
| DNS indisponible / timeout | Le signal vaut « inconnu », affiché comme tel. Les autres signaux s'affichent. Jamais de 5xx. |
| PTR absent | « aucun reverse DNS » — en soi un signal : les gros routeurs légitimes en ont tous un. |
| PTR incohérent (FCrDNS échoué) | Affiché comme incohérent, ce qui est une information, pas une erreur. |
| SPF avec macro / `ptr` / `exists` | `indéterminé`, explicitement. Jamais un `fail` inventé. |
| SPF dépassant 10 requêtes DNS | `permerror` — c'est un vrai défaut du domaine, on le dit. |
| PTR matchant le catalogue mais FCrDNS échoué | **Non identifié.** Un PTR menteur ne doit pas blanchir un usurpateur. |
| IP inconnue du tenant | 404, avant toute interrogation DNS ou lecture du cache. |

## Tests

- **`test_ip_intel.py`** (résolveur DNS moqué) : PTR nominal ; FCrDNS incohérent ;
  parsing des réponses Team Cymru (IPv4 et IPv6) ; chaîne `include` ; **limite des 10
  requêtes DNS** ; macro → `indéterminé` ; timeout → « inconnu », pas d'exception.
- **`test_senders.py`** — le catalogue ne doit jamais mentir :
  - PTR `x.sendgrid.net` **avec FCrDNS vérifié** → identifié SendGrid, remédiation servie.
  - **Même PTR, FCrDNS échoué → NON identifié.** C'est le test qui compte : sans lui, un
    usurpateur pose un PTR menteur et notre écran le blanchit.
  - ASN seul (AS16509, PTR quelconque d'EC2) → « hébergé chez Amazon », **jamais**
    « Amazon SES ».
  - Expéditeur reconnu mais SPF non couvrant → verdict d'échec conservé, remédiation
    affichée. Le catalogue n'écrase pas les faits.
  - Aucune entrée ne matche → faits DNS bruts, aucune dégradation.
- **`test_ip_intel_api.py`** : 404 sur IP inconnue du tenant ; cache frais → aucune
  requête DNS ; cache périmé → réinterrogation ; résumé d'activité correct.
- **`tests/test_tenant_isolation.py`** (bloquant, à compléter) : **le tenant A qui
  interroge une IP vue uniquement par le tenant B reçoit 404** — pas un 200 appauvri, pas
  un 403 qui confirmerait l'existence.

## Ce qui reste hors de portée, et qu'on ne prétendra pas savoir

Aucun de ces signaux ne prouve une intention. Une IP inconnue au PTR incohérent chez un
hébergeur obscur est *probablement* une usurpation ; une IP de Google *peut* être un
compte Gmail compromis d'un employé. Le panneau doit présenter des faits et un faisceau,
jamais une accusation. Le verdict est une aide à la décision — pas la décision.
