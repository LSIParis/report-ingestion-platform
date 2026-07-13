# Ajouter un nouveau domaine à surveiller

Procédure complète, de la demande client au premier rapport visible dans le dashboard.
Aucun développement, aucun déploiement : deux enregistrements DNS et deux commandes.

**Temps de mise en œuvre** : 15 minutes de travail. **Premiers rapports** : sous 24 à 48 h
(les rapports DMARC sont envoyés une fois par jour par chaque fournisseur de messagerie).

---

## En deux mots : à quoi ça sert

Chaque jour, Google, Microsoft, Yahoo et les autres reçoivent des e-mails qui prétendent
venir du domaine de ton client. Ils vérifient si ces messages sont authentiques, et
peuvent envoyer un **rapport quotidien** de ce qu'ils ont constaté — à condition qu'on
leur dise où l'envoyer.

C'est ce qu'on fait ici : on demande à ces rapports d'arriver dans une boîte que la
plateforme relève automatiquement. Elle les décompresse, les lit, et affiche pour chaque
client : qui envoie du mail en son nom, depuis quelles adresses IP, et combien de ces
messages passent ou échouent les contrôles d'authenticité.

Le client ne voit **que son propre domaine**. Jamais celui d'un autre.

---

## Vue d'ensemble

| # | Étape | Qui | Où |
|---|-------|-----|-----|
| 1 | Publier l'enregistrement DMARC | l'administrateur DNS **du client** | zone DNS du client |
| 2 | Autoriser la collecte externe | **toi** | zone DNS de `lsiparis.tech` |
| 3 | Créer le domaine dans l'application | **toi** | console Portainer |
| 4 | Créer le compte du client | **toi** | console Portainer |
| 5 | Vérifier | **toi** | dashboard |

---

## Étape 1 — L'enregistrement DMARC (côté client)

C'est la seule étape qui dépend d'un tiers : elle se fait dans la zone DNS **du client**
(chez son hébergeur : OVH, Cloudflare, Gandi…). Sans elle, aucun rapport n'existe.

Enregistrement à créer :

| Champ | Valeur |
|---|---|
| **Type** | `TXT` |
| **Nom / Hôte** | `_dmarc` |
| **Valeur** | `v=DMARC1; p=none; rua=mailto:dmarc.lsi@lsiparis.tech; fo=1; adkim=s;` |

### ⚠️ Commencer en `p=none`, toujours

Le paramètre `p=` dit aux fournisseurs quoi faire d'un message qui échoue les contrôles :

- `p=none` — **ne rien faire**, juste envoyer le rapport. **C'est par là qu'on commence.**
- `p=quarantine` — mettre en indésirables.
- `p=reject` — refuser le message.

Passer directement en `p=reject` sur un domaine jamais audité **fait disparaître du mail
légitime** : newsletters, facturation, CRM, outils tiers qui envoient au nom du client sont
très souvent mal configurés sans que personne ne le sache. C'est précisément ce que les
rapports servent à découvrir.

**La bonne séquence** : `p=none` → on observe 2 à 4 semaines dans le dashboard → on corrige
ce qui est cassé → puis seulement on durcit vers `quarantine`, puis `reject`.

---

## Étape 2 — Autoriser la collecte externe (côté `lsiparis.tech`)

La boîte de collecte (`dmarc.lsi@lsiparis.tech`) n'appartient pas au domaine du client. La
norme (RFC 7489 §7.1) exige donc que **notre** domaine déclare qu'il accepte de recevoir
les rapports du sien. C'est ce qui empêche n'importe qui de nous faire inonder de rapports
en pointant vers notre boîte.

Dans la zone DNS de `lsiparis.tech` :

| Champ | Valeur |
|---|---|
| **Type** | `TXT` |
| **Nom / Hôte** | `nouveau-client.com._report._dmarc` |
| **Valeur** | `v=DMARC1` |

(Remplacer `nouveau-client.com` par le domaine réel, **avec** son extension.)

> **À savoir** : aujourd'hui, Google et Microsoft n'appliquent pas ce contrôle — nos domaines
> actuels n'ont pas cet enregistrement et reçoivent quand même leurs rapports. Mais rien ne
> garantit que ça durera, et d'autres fournisseurs l'appliquent. Publie-le : c'est une ligne,
> et ça supprime une panne silencieuse possible.

---

## Étape 3 — Créer le domaine dans l'application

Dans **Portainer** → conteneur `dmarc-reports-api` → **Console** → `/bin/sh` → *Connect*.

```bash
python -m scripts.add_tenant nouveau-client.com "Nouveau Client SA"
```

Cette commande crée le client **et** la règle qui reconnaît ses rapports. Elle est
rejouable sans risque (si le domaine existe déjà, elle ne fait rien).

---

## Étape 4 — Créer le compte du client

```bash
USER_PASSWORD='choisis-un-mot-de-passe-fort' \
  python -m scripts.add_user dmarc@nouveau-client.com tenant_viewer nouveau-client.com
```

- `tenant_viewer` — ne voit **que** les domaines auxquels il est rattaché.
- `platform_admin` — voit tout, y compris les rapports non attribués.

Un même compte peut suivre plusieurs domaines : il suffit de les lister à la suite.

```bash
USER_PASSWORD='...' python -m scripts.add_user dsi@groupe.com tenant_viewer filiale-a.com filiale-b.com
```

Transmets les identifiants par un canal sûr, et demande au client de changer le mot de passe.

---

## Étape 5 — Si des rapports sont déjà arrivés

Si le client avait publié son DMARC **avant** l'étape 3, ses rapports sont déjà dans la
boîte — mais la plateforme ne savait pas à qui les attribuer, donc elle les a mis **en
quarantaine** plutôt que de deviner. Ils sont invisibles de tous.

Une fois le domaine créé, on les rejoue :

```bash
python -m scripts.requeue needs_review
```

Ils sont alors rattachés au bon client et apparaissent dans son dashboard. Rien n'est perdu :
l'e-mail d'origine est toujours conservé intact.

---

## Étape 6 — Vérifier

1. **Le DNS est bon** (immédiat) — sur https://mxtoolbox.com/dmarc.aspx, saisir le domaine :
   l'enregistrement doit s'afficher avec `rua=mailto:dmarc.lsi@lsiparis.tech`.
2. **Les rapports arrivent** (sous 24-48 h) — se connecter sur
   https://dmarc-reports.lsiparis.tech avec le compte du client : les premiers rapports
   apparaissent.
3. **L'isolation tient** — le client ne voit que son domaine. C'est garanti à trois niveaux
   (jeton signé, contrôle d'accès, et la base de données elle-même), et vérifié à chaque
   modification du code.

---

## Ce que le client verra

Pour chaque journée et chaque fournisseur (Google, Outlook…) :

| Information | Ce que ça veut dire |
|---|---|
| **Adresse IP source** | qui a envoyé du mail au nom du domaine |
| **Nombre de messages** | combien |
| **DKIM / SPF** | les deux contrôles d'authenticité : `pass` ou `fail` |
| **Résultat** | le message est authentique si **au moins un** des deux passe |
| **Disposition** | ce que le fournisseur en a fait (rien / indésirable / rejeté) |

Une IP inconnue qui échoue les deux contrôles, c'est soit un **outil légitime mal
configuré** (le cas le plus fréquent), soit quelqu'un qui **usurpe le domaine**. C'est
exactement ce qu'on cherche à voir.

---

## En cas de problème

| Symptôme | Cause probable | Que faire |
|---|---|---|
| Aucun rapport après 48 h | L'enregistrement DMARC n'est pas publié, ou mal nommé | Vérifier sur mxtoolbox que `_dmarc.<domaine>` répond |
| Aucun rapport, DNS correct | Le domaine n'envoie tout simplement pas de mail | Normal : sans trafic, pas de rapport |
| Les rapports restent invisibles | Le domaine n'a pas été créé dans l'application (étape 3) | Faire l'étape 3, puis l'étape 5 |
| Le client ne voit rien en se connectant | Son compte n'est pas rattaché au bon domaine | Rejouer l'étape 4 |
| Rapports « en quarantaine » | Arrivés avant la création du domaine | Étape 5 |

---

## Deux règles à ne pas contourner

**Ne jamais créer de règle de type `sender`.** Les rapports DMARC viennent *toujours* de
Google ou Microsoft, quel que soit le domaine concerné. Une règle basée sur l'expéditeur
enverrait donc les rapports de **tous les clients dans un seul dossier**. Le domaine ne se
lit que dans le sujet du message — c'est ce que fait `add_tenant`, et lui seul.

**En cas de doute, la plateforme ne devine pas.** Un rapport qu'elle ne sait pas attribuer
part en quarantaine, invisible de tous, plutôt que d'être rattaché au mauvais client. Et si
le sujet d'un message annonce un domaine mais que son contenu en concerne un autre, **rien
n'est enregistré** : le sujet d'un e-mail est falsifiable par n'importe qui, le croire sur
parole permettrait d'injecter de fausses données dans le dossier d'un client.
