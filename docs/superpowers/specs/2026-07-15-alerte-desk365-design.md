# Canal d'alerte Desk365 — créer un ticket au lieu d'un webhook

**Date** : 2026-07-15
**Statut** : validé, prêt pour le plan d'implémentation
**Prérequis** : le système d'alertes (spec `2026-07-14-alertes-design.md`), déjà livré.

## Le problème

Le système d'alertes pousse aujourd'hui ses événements sur un **webhook générique**
(`ALERT_WEBHOOK_URL`). LSI Paris gère son support dans **Desk365** : plutôt qu'un webhook
à relayer, une alerte critique doit **ouvrir directement un ticket** dans l'outil où les
techniciens travaillent déjà, avec les bons paramètres (groupe, priorité, catégorie).

## Ce qu'on construit

Un **deuxième canal** d'alerte, sélectionnable par configuration : au lieu de poster sur
un webhook, il crée un ticket via l'API Desk365. Tout le couplage au fournisseur reste
enfermé dans un seul fichier ; le reste du système d'alertes ne change pas.

## Architecture

### 1. Le canal devient sélectionnable — sans toucher au reste

Le système d'alertes appelle déjà un point unique : `webhook.envoyer(event, alert,
tenant) -> bool`, avec l'idempotence (`opened_notified_at` / `closed_notified_at`) et les
retries **au-dessus**, dans `workers/tasks.py`. On généralise cette frontière en un
**canal** interchangeable :

```
app/services/alerting/channels/
    __init__.py        # get_channel() -> lit ALERT_CHANNEL, renvoie le bon module
    base.py            # le contrat + CanalIndisponible
    webhook.py         # l'existant, deplace ici tel quel
    desk365.py         # le nouveau
```

- **Contrat** : `envoyer(event, alert, tenant) -> bool`. Renvoie `True` si quelque chose
  a été émis, `False` si le canal n'est pas configuré **ou** si l'événement ne doit rien
  produire (voir §2). Lève `CanalIndisponible` si le canal est configuré mais l'appel
  externe échoue (Celery retentera).
- **Sélection** : `ALERT_CHANNEL = webhook | desk365`, défaut **`webhook`** (rien ne
  change pour l'existant). « Au lieu d'un webhook » = mettre `desk365`.
- `workers/tasks.py` remplace son import direct `from … import webhook` par
  `canal = get_channel()` et appelle `canal.envoyer(...)`. **L'idempotence, le filet de
  rattrapage et les retries ne changent pas d'une ligne** — ils vivent au-dessus du canal.

Le renommage `webhook.py` → `channels/webhook.py` est un déplacement à l'identique
(`WebhookIndisponible` devient une sous-classe de `CanalIndisponible`, ou l'inverse — un
seul type d'exception partagé par les canaux). L'exception commune est ce que
`workers/tasks.py` attrape.

### 2. Cycle de vie du ticket

Un ticket a un cycle plus long qu'une notification : il naît, il vit, on l'annote. On le
calque sur les deux événements d'une alerte.

**À l'ouverture d'une alerte CRITIQUE** :

- `POST v3/tickets/create` → on crée le ticket.
- On **stocke son numéro** dans une nouvelle colonne `alert.external_ref` (migration
  `0009`). C'est ce numéro qui permettra d'annoter le ticket à la fermeture.

**À l'ouverture d'une alerte non critique** (avertissement — `tls_failure` en mode
`testing`, où rien n'est encore bloqué) : **rien**. `envoyer` renvoie `False`. L'alerte
reste visible sur la page Alertes, elle n'inonde pas le support d'un ticket Urgent pour
une non-urgence.

**À la fermeture d'une alerte** :

- Si `external_ref` est renseigné (un ticket avait été créé) → on **ajoute une note** au
  ticket (« Condition résolue le … — vérifiez puis clôturez »). On ne clôture pas
  automatiquement : le technicien vérifie et clôture lui-même.
- Si `external_ref` est vide (jamais de ticket — avertissement, ou Desk365 indisponible à
  l'ouverture) → **rien**, proprement. `envoyer` renvoie `False`.

Cas de l'escalade `testing → enforce` : la mécanique du réconciliateur ferme l'alerte
`warning` (pas de ticket, `external_ref` vide → note ignorée) et **ouvre une alerte neuve
`critical`** (nouveau ticket). Rien de spécial à coder : c'est le comportement existant.

### 3. Quelles alertes créent un ticket

Seulement les **critiques** :

- `never_reported` (critical) — un client jamais protégé.
- `domain_silent` (critical) — un domaine devenu muet.
- `tls_failure` **en mode `enforce`** (critical) — du courrier réellement refusé.

Le détecteur `tls_failure` en `testing` est `warning` : pas de ticket. La sélection se
fait sur `alert.severity == "critical"`, pas sur le `kind` — ainsi un futur détecteur
critique obtient un ticket sans code supplémentaire.

### 4. Contenu et paramètres du ticket

Paramètres fixes, exposés en **configuration** avec les valeurs par défaut ci-dessous
(modifiables sans redéploiement de code) :

| Config | Défaut | Champ Desk365 |
|---|---|---|
| `DESK365_BASE_URL` | `https://lsi-maintenance.desk365.io/apis/v3` | base d'API |
| `DESK365_API_KEY` | *(vide)* | en-tête `Authorization` |
| `DESK365_REQUESTER_EMAIL` | `alerte_dmarc@lsiparis.tech` | `contact_email` |
| `DESK365_GROUP` | `Support informatique` | `group` |
| `DESK365_PRIORITY` | `20` | `priority` (20 = Urgent) |
| `DESK365_CATEGORY` | `Réseau` | `category` |
| `DESK365_SUBCATEGORY` | `Déliverabilité emails` | `sub_category` |

Groupe / catégorie / sous-catégorie sont passés **par nom** (l'API Desk365 les accepte
ainsi, pas par ID). Priorité par valeur numérique.

`subject` et `description` sont **dérivés de l'alerte**, en français d'exploitant :

- Sujet : `[DMARC] <domaine> — <libellé de la nature de l'alerte>`
  (ex. « [DMARC] client.fr — aucun rapport reçu depuis l'ajout du domaine »).
- Description : le domaine, ce que le détecteur a constaté (déplié depuis `alert.payload`,
  pas le JSON brut), et un renvoi vers la page Alertes de la plateforme.

Les trois natures d'alerte réutilisent les mêmes libellés « quoi faire » que la page
Alertes (`frontend/src/pages/Alerts.tsx`) — une seule voix, deux surfaces.

### 5. L'API Desk365 (ce qui est connu)

- Base : `https://lsi-maintenance.desk365.io/apis/v3/`.
- Auth : en-tête `Authorization: <clé>` (clé depuis Desk365 → Settings → Integrations →
  API).
- Créer : `POST v3/tickets/create` avec `contact_email`, `subject`, `description`,
  `group`, `priority`, `category`, `sub_category`.
- Annoter : l'action « ajouter une note » de l'API v3, par **numéro de ticket**. Le chemin
  exact (`v3/tickets/add_note` ou équivalent) et le nom des champs seront **confirmés
  contre l'OpenAPI du sous-domaine** (`…/apis/api-docs.html`) au moment du plan, puis
  éprouvés par le test réel (§7).
- Limite : 100 appels/heure (plan standard) — très large devant le volume d'alertes
  critiques.
- Le `contact_email` du demandeur (`alerte_dmarc@lsiparis.tech`) : à vérifier en réel — la
  plupart des helpdesks créent le contact à la volée, mais Desk365 peut exiger qu'il
  existe. Point de contrôle du test réel.

Aucune nouvelle dépendance : `urllib.request` de la bibliothèque standard, comme le canal
webhook et `services/onboarding.py`.

### 6. Erreurs et robustesse — les règles du système d'alertes, inchangées

| Situation | Comportement |
|---|---|
| `DESK365_API_KEY` ou base non configurée | `envoyer` renvoie `False`, **journalisé**. Non configuré ≠ silencieux. |
| API Desk365 en panne / 5xx / timeout | `CanalIndisponible` levée → retry Celery. L'alerte reste ouverte en base ; **on perd une notification, jamais une alerte**. |
| Alerte non critique | `envoyer` renvoie `False`, aucun ticket. |
| Fermeture sans `external_ref` | Rien, proprement. |
| Ticket déjà créé (tâche rejouée) | `opened_notified_at` déjà posé → l'événement n'est pas rejoué. Pas de doublon. |
| Réponse `create` sans numéro de ticket exploitable | `CanalIndisponible` (on ne peut pas garantir le suivi) → retry, et on ne pose pas `external_ref` à tort. |

L'idempotence repose sur les colonnes existantes (`opened_notified_at` /
`closed_notified_at`) **plus** `external_ref` comme preuve qu'un ticket existe. Un ticket
n'est créé qu'une fois par alerte.

### 7. Tests

- **`test_desk365.py`** (API moquée) : création sur ouverture critique (bons champs, bon
  demandeur, priorité 20) ; `external_ref` stocké depuis la réponse ; note sur fermeture
  quand `external_ref` existe ; **rien** sur un avertissement ; **rien** sur fermeture sans
  `external_ref` ; non configuré → `False` + journalisé ; 5xx / timeout →
  `CanalIndisponible`.
- **`test_channels.py`** : `get_channel()` renvoie le bon module selon `ALERT_CHANNEL` ;
  un `ALERT_CHANNEL` inconnu lève une erreur claire au démarrage (pas un silence).
- **Non-régression** : les tests existants du canal webhook passent après le déplacement
  dans `channels/webhook.py`, sans modification de comportement.
- **Test réel de bout en bout** (fin de plan) : avec une vraie clé API, ouvrir une alerte
  critique de test → vérifier qu'un ticket **apparaît réellement dans Desk365** avec les
  bons paramètres, puis fermer l'alerte → vérifier que **la note apparaît sur ce ticket**.
  Sur ce projet, on a vu ce que vaut ce qui n'a pas été vérifié en vrai.

## Configuration en production

Le worker (et le service `beat`) reçoivent les nouvelles variables. Le canal se choisit
avec `ALERT_CHANNEL=desk365` ; la clé `DESK365_API_KEY` est un **secret**, fourni comme
variable d'environnement de la stack, jamais committé. Les paramètres fixes (groupe,
priorité…) ont leurs défauts et n'ont pas à être renseignés sauf changement.

## Ce qu'on ne fait pas, délibérément

- **Clôturer automatiquement le ticket.** Une alerte qui se ferme puis rouvre (bruit)
  ballotterait le ticket ; et clôturer sans qu'un humain ait vérifié est le contraire de
  ce qu'un support veut. On annote, l'humain clôture.
- **Un canal multi-diffusion** (webhook ET Desk365 en même temps). On choisit un canal. Si
  le besoin des deux apparaît, l'abstraction `channels/` le permettra sans refonte — mais
  YAGNI aujourd'hui.
- **Mapper la sévérité vers la priorité Desk365.** Seules les critiques créent un ticket,
  et elles sont toutes Urgent (valeur demandée). Pas de tableau de correspondance à
  maintenir.
- **Synchroniser l'état du ticket vers la plateforme** (savoir si le technicien a
  clôturé). La plateforme émet, Desk365 gère la vie du ticket. Pas de boucle de retour.
