# MTA-STS — politiques de chiffrement du courrier entrant

MTA-STS (RFC 8461) force les serveurs distants à **chiffrer** le courrier qu'ils t'envoient,
et à **vérifier le certificat** de ton MX. Sans lui, STARTTLS est opportuniste : un attaquant
en position d'intermédiaire peut le supprimer de la négociation et lire le courrier en clair.

> ⚠️ **C'est le seul mécanisme de cette famille qui peut faire perdre du courrier.**
> En mode `enforce`, un expéditeur qui n'obtient pas un certificat valide correspondant à
> ton MX **refuse la livraison**. D'où la procédure ci-dessous : on passe par `testing`, on
> observe, et on ne durcit qu'ensuite.

## Comment ça marche

Trois pièces, et il faut les trois :

| Pièce | Où | Rôle |
|---|---|---|
| `_mta-sts.<domaine>` TXT | zone DNS du domaine | annonce qu'une politique existe, et sa version (`id`) |
| `https://mta-sts.<domaine>/.well-known/mta-sts.txt` | **ce service** | la politique elle-même |
| `mta-sts.<domaine>` A | zone DNS du domaine | fait pointer ce nom vers l'hôte qui sert la politique |

Un expéditeur lit le TXT, va chercher la politique en HTTPS, la met en cache pour `max_age`
secondes, puis l'applique. **Si la politique est injoignable, il n'y a simplement pas de
politique** — c'est pourquoi un enregistrement TXT sans fichier servi est inerte, pas
dangereux.

## Le service

Un nginx qui déduit le domaine du `Host` : `mta-sts.exemple.com` → `/srv/policies/exemple.com.txt`.
Un domaine non servi retourne **404**, jamais la politique d'un autre.

Les politiques sont **embarquées dans l'image** (`policies/*.txt`) : sur un endpoint Portainer
**agent**, un bind-mount depuis le dépôt cloné ne fonctionne pas (le dépôt vit sur le serveur
Portainer, pas sur l'hôte Docker). Modifier une politique = commit → l'image est reconstruite
→ redéployer la stack.

## Ajouter un domaine

1. Créer `policies/<domaine>.txt` :

   ```
   version: STSv1
   mode: testing
   mx: <le ou les MX, un par ligne>
   max_age: 86400
   ```

   Le `mx:` doit correspondre au **certificat** présenté par le MX, pas seulement à son nom.
   Vérifier avant de publier :

   ```bash
   echo QUIT | openssl s_client -starttls smtp -connect <mx>:25 -servername <mx> \
     | openssl x509 -noout -subject -ext subjectAltName
   ```

   - MX sur Microsoft 365 → `mx: *.mail.protection.outlook.com`
   - MX auto-hébergé → le nom exact couvert par le certificat.

2. Commit + push : la CI reconstruit l'image. Redéployer la stack (Portainer → *Pull and redeploy*).

3. DNS du domaine : `mta-sts` A → IP de l'hôte (**DNS-only**, pas de proxy).

4. NPM : Proxy Host `mta-sts.<domaine>` → `http://mta-sts:80`, avec certificat Let's Encrypt.

5. DNS du domaine : `_mta-sts` TXT → `v=STSv1; id=<horodatage>`.

6. Vérifier :

   ```bash
   curl -sSi https://mta-sts.<domaine>/.well-known/mta-sts.txt
   ```

   Doit répondre **200**, en `text/plain`, **sans redirection**.

## Passer en enforce

**Ne pas le faire tout de suite.** La séquence :

1. Rester en `mode: testing` au moins **une à deux semaines**.
2. Surveiller les rapports **TLS-RPT** (c'est exactement leur raison d'être) : ils signalent
   les échecs de négociation TLS *sans bloquer le courrier*.
3. Zéro échec sur la période → passer `mode: enforce` et augmenter `max_age` (604800 = 7 jours).
4. **Changer l'`id` du TXT à chaque modification de politique**, sinon les expéditeurs
   gardent l'ancienne en cache jusqu'à expiration de `max_age`.

## En cas d'incident

Une politique `enforce` erronée bloque le courrier entrant. Pour la neutraliser, du plus
rapide au plus lent :

1. **Repasser la politique en `mode: none`** et bumper l'`id` du TXT. Les expéditeurs qui
   relisent la politique cessent d'appliquer les contraintes.
2. Supprimer l'enregistrement TXT `_mta-sts` — mais les politiques **déjà en cache** restent
   actives jusqu'à `max_age`. C'est précisément pourquoi on commence avec un `max_age` court
   (86400 = 1 jour) et qu'on ne l'allonge qu'une fois la politique éprouvée.

Un `max_age` long est une bonne pratique de sécurité **et** une prise de risque : on ne peut
pas le raccourcir rétroactivement chez ceux qui ont déjà mis la politique en cache.
