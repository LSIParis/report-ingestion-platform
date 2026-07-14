# Ingestion et exploitation des rapports TLS-RPT (RFC 8460)

**Date** : 2026-07-14
**Statut** : validé, prêt pour le plan d'implémentation
**Périmètre** : chantier A (ingérer + exploiter). L'alerte est un cycle séparé — voir « Ce
qu'on ne fait pas ».

## Le problème

La procédure d'onboarding demande au client de publier `_smtp._tls` et de faire pointer
ses rapports TLS vers notre boîte. Ils arrivent. **Et rien ne sait les lire.**

La cause tient en une ligne (`app/workers/tasks.py:34`) :

```python
".gz": "dmarc_xml", ".zip": "dmarc_xml", ".xml": "dmarc_xml",
```

Un rapport TLS-RPT s'appelle `google.com!exemple.fr!1752…!1752….json.gz` — extension
`.gz`. Il part donc **à l'adaptateur DMARC**, qui le décompresse, y trouve du JSON, et
échoue en `DMARC_BAD_XML`. Les rapports TLS ne sont pas ignorés : ils sont **en `failed`
dans la base**, comptés comme des erreurs de parsing. Et un rapport TLS non compressé
(`.json`) n'entre même pas dans la table : il est ignoré, et le mail finit en `failed`
« no source ».

L'ironie est que le dépôt sait déjà que c'est une faute. Le `dmarc_adapter` écrit :
« Détection par nombre magique, pas par extension — le nom de fichier vient de
l'expéditeur, on ne lui fait pas confiance. » Mais l'aiguillage **en amont** fait
exactement l'inverse.

Conséquence opérationnelle : MTA-STS est en `testing` et le restera. Personne ne peut
décider de passer en `enforce`, faute de voir si le chiffrement fonctionne — et
`enforce` sur un domaine jamais observé, c'est risquer de faire refuser du courrier
légitime.

## Ce qui est déjà en place, et ne bougera pas

C'est la bonne nouvelle, et elle structure tout le travail : l'architecture attendait ce
format.

- **La résolution de tenant fonctionne déjà.** Le sujet d'un rapport TLS-RPT est
  « Report Domain: exemple.fr Submitter: google.com… », et la règle créée à l'ajout d'un
  domaine (`services/tenants.py`) cherche `domain:\s*<domaine>` en ignorant la casse.
  Elle matche. **Aucune règle à ajouter.**
- **Le garde anti-usurpation fonctionne déjà.** `guard_report_domain` recoupe
  `metadata["policy_domain"]` avec le domaine du tenant, et rejette tout désaccord. Le
  champ `policy-domain` de TLS-RPT s'y branche tel quel. **Zéro modification** : un
  rapport TLS forgé pour le domaine d'un autre client est rejeté par du code qui existe.
- **La sélection de profil fonctionne déjà.** `select_profile` retombe sur
  `_default_<fmt>` : il suffit d'un fichier `profiles/_default_tlsrpt_json.json`.
- **L'historique n'est pas perdu.** Le `.eml` brut est conservé en S3 (invariant). Les
  rapports TLS déjà tombés en `failed` seront relus correctement par `reprocess_report`.

## Architecture

### 1. `app/parsing/compression.py` — la décompression, sortie de l'adaptateur DMARC

Le `dmarc_adapter` porte aujourd'hui `decompress()` et ses bornes anti-bombe
(`MAX_XML_BYTES`, lecture par blocs). Le détecteur de format en a besoin **avant** de
savoir quel adaptateur appeler — donc avant de pouvoir demander au DMARC de le faire.

On l'extrait tel quel dans `app/parsing/compression.py` : `decompress(raw) -> bytes`,
gzip / zip / nu, détection par nombre magique, bornes conservées à l'identique.
`dmarc_adapter` l'importe. Aucun changement de comportement — c'est un déplacement.

### 2. `app/parsing/detect.py` — le contenu décide, pas le nom

```python
def detect_format(payload: bytes, filename: str | None) -> str | None
```

On décompresse, on saute les espaces, et on regarde le **premier octet significatif** :
`{` → `tlsrpt_json`, `<` → `dmarc_xml`. Rien d'exploitable → `None` (la pièce jointe est
ignorée, comme aujourd'hui).

L'extension ne sert plus qu'à écarter d'emblée ce qui ne peut pas être un rapport
(`.csv`, `.xlsx`, `.pdf` gardent leur aiguillage actuel, qui n'est pas ambigu). Pour tout
ce qui ressemble à une archive ou à un texte structuré (`.gz`, `.zip`, `.xml`, `.json`,
et **l'absence d'extension**), c'est le contenu qui tranche.

Effet de bord souhaitable : un rapport DMARC nommé de travers est désormais lu
correctement lui aussi.

### 3. `app/parsing/adapters/tlsrpt_adapter.py` — `@register("tlsrpt_json")`

Contrat inchangé : `parse(raw, profile) -> ParseResult`. L'orchestrateur n'est pas touché.

**Une ligne = une observation**, comme pour DMARC (où une ligne = un `<record>`). Le JSON
RFC 8460 contient N politiques, chacune avec un résumé et une liste d'échecs. On émet
donc deux natures de lignes :

| `kind` | Champs propres | Sens |
|---|---|---|
| `summary` | `successful_sessions`, `failed_sessions` | une par politique : le bilan chiffré des sessions |
| `failure` | `result_type`, `sending_mta_ip`, `receiving_mx_hostname`, `receiving_ip`, `failure_sessions` | une par échec détaillé |

Champs communs à toutes les lignes (repris de l'en-tête du rapport) : `reporter`
(`organization-name`), `report_id`, `report_date` (`date-range.start-datetime`),
`period_end`, `policy_domain`, `policy_type` (`sts` / `tlsa` / `no-policy-found`),
`mx_host`.

**Les compteurs portent des noms différents à dessein.** Si le résumé et le détail
s'appelaient tous deux `failed_sessions`, un `SUM()` sur la table double-compterait chaque
échec — une fois dans le résumé, une fois dans le détail — et la statistique la plus
regardée de l'écran serait fausse, sans que rien ne le signale. Le schéma rend l'erreur
impossible au lieu de compter sur la vigilance de celui qui écrira la requête.

Parsing **tolérant**, comme partout ailleurs : une politique corrompue n'invalide pas le
rapport entier (statut `partial`, erreur collectée par ligne). Un JSON illisible →
`failed` avec `TLSRPT_BAD_JSON`. Un `result-type` inconnu de la RFC est **conservé tel
quel** : on ne le traduit pas, on ne le devine pas.

### 4. `profiles/_default_tlsrpt_json.json`

Le mapping colonnes → schéma canonique. Une donnée, aucun code.

Attention : les champs propres à `summary` et à `failure` sont absents l'un chez l'autre.
Seuls les champs **communs** peuvent être marqués `required` — sinon la normalisation
rejetterait la moitié des lignes de chaque rapport.

### 5. `GET /tls/posture?days=30` — le verdict

Route tenant (`get_db`, session déjà scopée : la RLS fait le travail, aucun
`WHERE tenant_id` applicatif). Elle agrège les lignes TLS de la fenêtre :

```json
{
  "days": 30,
  "sessions_total": 12480,
  "sessions_ok": 12480,
  "sessions_failed": 0,
  "failures": [],
  "safe_to_enforce": true,
  "reporters": ["Google Inc.", "Microsoft Corp."]
}
```

`failures` : `[{result_type, sessions, sending_mta_ip, receiving_mx_hostname}]`, trié par
volume décroissant.

`safe_to_enforce` vaut `true` **seulement si** on a des données ET aucun échec :
l'absence de rapport n'est pas une preuve de succès. Un domaine sans aucun rapport TLS
reçu doit être dit tel quel — « on ne sait pas » — et surtout pas « c'est sûr ».

### 6. L'écran : le verdict va là où se prend la décision

Pas de nouvelle page. Le verdict s'affiche **dans `MtaStsPanel`**, à côté du sélecteur
`none / testing / enforce` — parce que c'est là, et nulle part ailleurs, qu'on prend la
décision que ces rapports existent pour éclairer.

- **Aucun rapport reçu** : « Aucun rapport TLS reçu. Publiez `_smtp._tls` (voir la
  procédure), sinon vous durcirez à l'aveugle. » Ton neutre, jamais rassurant.
- **Sessions, aucun échec** : « 12 480 sessions sur 30 jours, 100 % chiffrées, aucun
  échec. Le passage en `enforce` est sûr. »
- **Échecs** : « ⚠ 3 échecs `certificate-host-mismatch` depuis `mx-backup.exemple.fr`. En
  `enforce`, ces messages seraient **refusés**. Corrigez d'abord. » Le compte, le type et
  la source — pas un pourcentage abstrait.

Et le détail d'un rapport TLS obtient un rendu lisible dans `ReportDetail`, comme les
lignes DMARC (aujourd'hui : vidage du JSON brut).

### 7. Enrichissement de l'IP émettrice — et le piège qu'il ouvre

Une IP qui échoue en TLS mérite la même enquête qu'une IP rejetée en DMARC : le panneau
`IpPanel` est réutilisé, `sending_mta_ip` devient cliquable.

**Mais le contrôle d'appartenance de `/ip-intel/{ip}` cherche `data->>'source_ip'`** — et
une ligne TLS porte `sending_mta_ip`. Sans extension, cliquer une IP TLS renverrait **404
sur une IP que le tenant voit pourtant dans ses propres rapports**. Il faut donc :

- étendre le contrôle : `data->>'source_ip' = :ip OR data->>'sending_mta_ip' = :ip`,
  toujours **sous RLS** (le principe ne change pas : on n'enquête que sur une IP déjà
  légitimement vue) ;
- ajouter l'index correspondant : `ix_report_row_sending_mta_ip` — migration `0005` ;
- enrichir le résumé d'activité : `activity` gagne `tls_failures`
  (`{result_type: sessions}`) et `tls_sessions`. **Sans ça, le panneau afficherait
  « 0 message » sur une IP vue uniquement en TLS** — ce qui est faux et donnerait à croire
  qu'elle n'a rien fait.

On ne renomme **pas** `sending_mta_ip` en `source_ip` pour simplifier : les deux champs ne
disent pas la même chose (l'un est un expéditeur évalué par DMARC, l'autre un MTA qui a
tenté une session TLS), et le front distingue aujourd'hui une ligne DMARC par la présence
de `source_ip`. Les confondre casserait le rendu et mentirait sur la sémantique.

## Erreurs et dégradation

| Situation | Comportement |
|---|---|
| JSON malformé | `failed`, `TLSRPT_BAD_JSON`. Le `.eml` reste en S3, rejouable. |
| Politique corrompue parmi d'autres | `partial` : les politiques valides sont gardées, l'erreur est collectée. |
| `policy-domain` ≠ domaine du tenant | Rejeté par `guard_report_domain` (code existant), aucune ligne écrite. |
| `result-type` inconnu de la RFC | Conservé tel quel. On n'invente pas de traduction. |
| Aucun rapport TLS reçu | « On ne sait pas » — jamais « c'est sûr ». `safe_to_enforce: false`. |
| Archive suspecte (bombe) | Bornes de `compression.py`, inchangées. |

## Tests

- **`test_tlsrpt_adapter.py`** : rapport nominal (2 politiques, échecs et succès) ;
  rapport sans aucun échec ; `failure-details` absent ; JSON malformé → `failed` propre ;
  `result-type` inconnu → conservé ; **`policy-domain` d'un autre domaine → rejeté par le
  garde existant** (le test le prouve, il ne le suppose pas).
- **`test_detect.py`** : `.gz` contenant du XML → `dmarc_xml` ; `.gz` contenant du JSON →
  `tlsrpt_json` ; `.json` nu → `tlsrpt_json` ; **extension mensongère (`.xml` contenant du
  JSON) → le contenu gagne** ; contenu inexploitable → `None`.
- **`test_tls_posture.py`** : agrégation correcte ; **pas de double comptage des échecs**
  (le test qui justifie les noms de compteurs distincts) ; aucun rapport →
  `safe_to_enforce: false`.
- **`test_tenant_isolation.py`** (bloquant) : le posture TLS d'un tenant est invisible
  d'un autre ; une IP vue uniquement dans les lignes TLS de B donne 404 chez A.

## Ce qu'on ne fait pas

- **L'alerte.** C'est un sous-système, pas une fonctionnalité : canal (e-mail sortant ?
  webhook ? bandeau ?), destinataire (les utilisateurs du domaine ? l'admin de la
  plateforme ?), seuil, et déduplication. Aucune de ces réponses ne se déduit du code
  actuel, et **le seuil pertinent ne se choisit qu'en regardant de vrais rapports** —
  qu'on n'a pas encore. Cycle dédié, après celui-ci. Bonne nouvelle pour plus tard : elle
  n'aura pas besoin d'ordonnanceur, le worker s'exécute déjà à l'arrivée de chaque
  rapport.
- **Le décodage des `policy-string`.** On les stocke, on ne les interprète pas.
- **Une page « chiffrement » dédiée.** Le verdict va dans `MtaStsPanel`, là où se prend la
  décision. Une page de plus qu'il faut penser à ouvrir ne servirait personne.
