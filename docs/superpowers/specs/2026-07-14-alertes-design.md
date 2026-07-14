# Alertes — la colonne vertébrale et le webhook

**Date** : 2026-07-14
**Statut** : validé, prêt pour le plan d'implémentation
**Périmètre** : chantier B1. Le canal e-mail vers le client est un cycle séparé — voir
« Ce qu'on ne fait pas ».

## Le problème

Le dépôt est parsemé de commentaires qui regrettent l'absence d'alerte. Le plus éloquent
est dans `services/onboarding.py` :

> « Aucune de ces erreurs ne produit d'alerte : elles se traduisent seulement par des
> rapports qui n'arrivent jamais. »

C'est le cœur du sujet. Les pannes que ce produit existe pour attraper sont **silencieuses
par nature** :

- un client publie mal son `_dmarc`, ou le supprime en « faisant le ménage » : les
  rapports cessent d'arriver. Aucun écran ne le montrera jamais — **il ne s'y passe
  rien** ;
- un domaine est ajouté, la procédure n'est jamais terminée, et personne ne s'en aperçoit.
  Le client se croit protégé. Il ne l'est pas.

L'échec TLS, lui, est bruyant : il finit par apparaître dans le tableau de bord. C'est le
seul des trois signaux qui se voyait déjà — et ce serait une erreur de construire un canal
d'alerte pour lui seul.

## Ce qu'on construit

Trois détecteurs, un réconciliateur, un canal webhook, un écran. Le canal e-mail est conçu
mais pas construit ici (la boîte d'envoi n'existe pas encore côté serveur de messagerie).

## Architecture

### 1. Une alerte est un ÉTAT, pas un message

C'est la décision qui gouverne tout le reste, et elle règle la déduplication sans qu'on
ait à y penser.

Une alerte n'est pas « un message a été envoyé ». C'est **une condition vraie, qui s'ouvre
et qui se ferme**.

```
alert(id, tenant_id, kind, dedup_key, severity, payload, opened_at, closed_at, notified_at)
```

Un **réconciliateur** compare, à chaque passage, les conditions actuellement vraies aux
alertes ouvertes :

- condition vraie sans alerte ouverte → **on ouvre**, on notifie ;
- alerte ouverte dont la condition a disparu → **on ferme**, on notifie ;
- condition vraie avec une alerte déjà ouverte → **on ne fait rien**.

La déduplication n'est pas une règle qu'on ajoute : c'est une **conséquence du modèle**.
Le réarmement est gratuit — un échec qui disparaît puis revient ferme une alerte et en
rouvre une autre, avec sa propre date.

Deux corollaires, tous deux voulus :

- **Le réconciliateur est idempotent.** On peut donc le lancer aussi souvent qu'on veut,
  sans se demander si on va spammer.
- **La base est la source de vérité**, pas le canal. Une alerte s'ouvre même si le webhook
  est en panne ou pas configuré. L'écran la montrera.

**Garantie au niveau de la base**, pas de la convention :

```sql
CREATE UNIQUE INDEX ux_alert_ouverte ON alert (tenant_id, kind, dedup_key)
  WHERE closed_at IS NULL;
```

Une seule alerte ouverte par condition — imposé par PostgreSQL. Un bug du réconciliateur
ne peut pas produire de doublon : il produit une erreur.

**RLS** : `alert` porte un `tenant_id`, donc `ENABLE` **et** `FORCE`, comme toute table
métier (invariant n°1). Aucune exception cette fois.

### 2. Trois détecteurs, choisis pour ne PAS se ressembler

Un cadre validé par un seul cas n'est pas un cadre. Ces trois-là ont des formes
délibérément différentes : l'un lit des lignes, l'autre lit une absence, le troisième lit
une date de création.

Un détecteur est une fonction pure : `detect(db, tenant) -> list[Condition]`, où
`Condition = (kind, dedup_key, severity, payload)`. En ajouter un = un fichier, comme un
adaptateur de parsing ou un profil.

**`tls_failure`** — des échecs de chiffrement dans la fenêtre. Alimenté par
`services/tls_posture.posture()`, déjà écrit et éprouvé. Une condition par triplet
`(result_type, sending_mta_ip, receiving_mx_hostname)` — c'est ce triplet qui dit à
l'exploitant quoi corriger, donc c'est lui la clé de déduplication.

*La sévérité dépend du mode MTA-STS du domaine, et c'est tout l'intérêt* :

- domaine en `testing` → **`warning`**. Les expéditeurs signalent, rien n'est bloqué.
- domaine en `enforce` → **`critical`**. Les mêmes données, une urgence radicalement
  différente : **du courrier est en train d'être refusé, maintenant.**

**`domain_silent`** — le domaine a reçu au moins un rapport dans les 30 jours précédents,
et plus aucun depuis `ALERT_SILENCE_DAYS` (défaut : 4). `dedup_key` vide : une seule
alerte de silence par domaine. Sévérité `critical` — un domaine silencieux est un domaine
dont on ne sait plus rien.

*Faux positifs assumés, et dits* : un domaine à très faible trafic peut légitimement
passer quelques jours sans rapport. On accepte ce bruit, délibérément : un faux positif
coûte un coup d'œil, un faux négatif laisse un client sans protection pendant des mois.
L'alerte se referme d'elle-même dès qu'un rapport arrive.

**`never_reported`** — le domaine est actif depuis plus de `ALERT_ONBOARDING_GRACE_DAYS`
(défaut : 7) et n'a **jamais** reçu le moindre rapport. Sévérité `critical`.

*La plus précieuse des trois.* Elle attrape le client qu'on croit protégé et qui ne l'est
pas — celui dont la procédure d'onboarding n'a jamais été terminée, et que rien, dans
l'application actuelle, ne distingue d'un client tranquille.

### 3. Le piège du balayage : la RLS ne se contourne pas par confort

`posture()` et les détecteurs **n'ont aucun filtre `tenant_id` applicatif** : ils comptent
sur la RLS. Le worker, lui, tourne en plan système (`BYPASSRLS`).

**Faire tourner un détecteur sur une session worker lui ferait voir TOUS les tenants** —
et ouvrirait les alertes d'un client sur le domaine d'un autre. C'est le genre de bug qui
ne se voit pas en développement (un seul tenant) et qui est catastrophique en production.

Le balayage ouvre donc **une `tenant_scoped_session(tenant_id=...)` par tenant**, sans
bypass. C'est l'option la plus restrictive, elle ne coûte rien, et un test le verrouille.

### 4. Quand ça tourne

**Un ordonnanceur est nécessaire, et je me suis trompé en affirmant le contraire.** Un
échec TLS arrive avec un e-mail — le worker tourne déjà. Mais **un domaine silencieux ne
produit aucun événement** : c'est sa définition même. On ne peut pas réagir à ce qui
n'arrive pas.

- **Celery Beat**, ajouté à la stack : un balayage **quotidien** de tous les domaines
  actifs.
- **Plus une réconciliation à l'ingestion**, pour le tenant concerné, quand un rapport
  vient d'être traité. Les alertes TLS apparaissent alors en minutes, pas le lendemain.

C'est gratuit précisément parce que le réconciliateur est idempotent : le faire tourner
deux fois n'a aucun effet de bord.

### 5. Le canal webhook

`POST` d'un JSON sur `ALERT_WEBHOOK_URL`. **URL générique, corps brut** : n8n, un script,
un endpoint à vous. Aucun couplage à un fournisseur — changer d'outil ne demande pas de
recoder.

```json
{
  "event": "opened",
  "at": "2026-07-14T18:00:00Z",
  "alert": {
    "id": "…", "kind": "never_reported", "severity": "critical",
    "dedup_key": "", "opened_at": "…",
    "payload": {"domain": "client.fr", "created_at": "2026-06-20", "reports": 0}
  },
  "tenant": {"id": "…", "domain": "client.fr"}
}
```

Trois règles :

- **L'envoi ne casse jamais le flux métier**, comme `audit()`. Un webhook injoignable est
  journalisé, retenté par Celery, et c'est tout. L'alerte reste ouverte en base : rien
  n'est perdu.
- **Non configuré ≠ silencieux.** Si `ALERT_WEBHOOK_URL` est vide, on le journalise
  explicitement. On n'avale pas les alertes sans le dire.
- Notification à l'ouverture **et** à la fermeture. Savoir qu'un problème s'est résolu vaut
  autant que d'apprendre qu'il existe.

### 6. L'écran

Une page **Alertes** (admin) : les alertes ouvertes, puis les récemment fermées, par
domaine — nature, sévérité, depuis quand, ce que le détecteur a vu.

Sans elle, une alerte fermée ne laisse aucune trace consultable, et le webhook devient la
seule mémoire du système. C'est aussi là que vivra, au cycle suivant, le bouton
« prévenir le client ».

## Configuration

| Clé | Défaut | Rôle |
|---|---|---|
| `ALERT_WEBHOOK_URL` | `""` | Vide → aucun envoi, mais les alertes s'ouvrent quand même. |
| `ALERT_SILENCE_DAYS` | `4` | Jours sans rapport avant de déclarer un domaine silencieux. |
| `ALERT_ONBOARDING_GRACE_DAYS` | `7` | Délai laissé à un nouveau domaine avant `never_reported`. |

## Erreurs et dégradation

| Situation | Comportement |
|---|---|
| Webhook injoignable / 500 | Retry Celery. L'alerte reste ouverte en base. Le pipeline n'est jamais cassé. |
| `ALERT_WEBHOOK_URL` non configuré | Alertes ouvertes normalement, absence d'envoi **journalisée**. Jamais un silence muet. |
| Deux réconciliations concurrentes | L'index unique partiel rejette le doublon. Une erreur, jamais une alerte en double. |
| Détecteur qui lève | L'exception est capturée par détecteur : un détecteur cassé ne prive pas des autres. Journalisé. |
| Domaine suspendu | Non balayé. Un domaine qu'on a coupé n'a pas à alerter. |

## Tests

- **`test_alert_reconciler.py`** : ouvre une alerte ; ne la rouvre pas au second passage ;
  la ferme quand la condition disparaît ; **la rouvre — avec une nouvelle date — si la
  condition revient**.
- **Index unique** : deux ouvertures concurrentes de la même condition → une seule alerte
  ouverte (garantie par la base, pas par le code).
- **`test_detectors.py`** : `tls_failure` (dont **`warning` en `testing` mais `critical` en
  `enforce`** — mêmes données, urgence différente) ; `domain_silent` (se ferme dès qu'un
  rapport arrive) ; `never_reported` (ne se déclenche pas pendant le délai de grâce).
- **`test_tenant_isolation.py`** (bloquant) : le tenant A ne voit pas les alertes de B ;
  et surtout — **le balayage ouvre bien une session scopée par tenant.** Un test qui sème
  des données chez B et vérifie qu'aucune alerte de B ne s'ouvre sur A. C'est le bug qui
  ne se voit pas avec un seul tenant.
- **`test_webhook.py`** : un webhook en échec ne casse pas le flux ; une URL non
  configurée journalise ; le corps JSON a la forme attendue.

## Ce qu'on ne fait pas

- **Le canal e-mail vers le client.** La boîte d'envoi n'existe pas encore côté serveur de
  messagerie — le canal se coderait, mais **ne se vérifierait pas en vrai**. Sur ce projet,
  on a déjà vu ce que vaut ce qui n'a pas été vérifié en vrai : une table de rendu a
  traversé une fonctionnalité entière sans jamais s'afficher. Cycle suivant, quand la boîte
  existera.

  Deux décisions sont déjà prises pour ce cycle-là, et méritent d'être écrites maintenant :
  1. **L'envoi au client ne sera pas automatique par défaut.** Un message automatique, vers
     la boîte d'un tiers, à propos de son domaine, sur un signal que personne n'a regardé —
     c'est ainsi qu'on grille sa réputation d'expéditeur. L'alerte s'ouvre, l'exploitant la
     voit, et **décide** de prévenir le client, avec un message pré-rédigé qu'il peut relire.
  2. **La plateforme refusera d'envoyer depuis un domaine qui n'est pas lui-même
     irréprochable.** On a déjà `spf.covers` et le vérificateur DNS d'onboarding : avant le
     premier envoi, on vérifie que le domaine d'expédition est aligné. Une plateforme de
     conformité e-mail dont les alertes finissent en spam serait une plaisanterie.

- **Slack/Teams formatés.** L'URL générique les couvre via n8n ou un script. Pas de
  couplage à un fournisseur.
- **Préférences par utilisateur, mise en veille, escalade.** On ne sait pas encore quelles
  alertes se déclenchent vraiment ni à quelle fréquence. On le saura en les regardant
  vivre.
- **Un « cadre d'alerte » abstrait.** Le cadre existe — un détecteur est un fichier — mais
  il a été éprouvé par trois cas qui ne se ressemblent pas, au lieu d'être imaginé à
  l'avance. Ajouter « quarantaine » ou « dead-letter » sera une donnée, pas une refonte.
