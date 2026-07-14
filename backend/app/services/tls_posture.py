"""« Puis-je passer MTA-STS en enforce sans perdre de courrier ? »

C'est LA question que les rapports TLS existent pour trancher, et la seule qui compte
devant le sélecteur de mode : en `enforce`, un expéditeur qui n'arrive pas à valider le
certificat **cesse de livrer** — sans alerte, sans trace de notre côté.

Deux pièges, tous deux mortels, tous deux évités ici :

 - **Ne pas double-compter les échecs.** Le résumé d'une politique dit « 3 échecs » ; le
   détail décrit ces mêmes 3 échecs. Sommer les deux donnerait 6. Les champs portent donc
   des noms distincts (`failed_sessions` / `failure_sessions`) : le total vient TOUJOURS
   des lignes `summary`, le détail ne sert qu'à dire quoi et qui.

 - **Ne pas confondre silence et succès.** Un domaine dont on n'a reçu aucun rapport n'est
   pas un domaine sans échec : c'est un domaine sur lequel on ne sait rien. Y répondre
   « c'est sûr » ferait durcir à l'aveugle — exactement ce que TLS-RPT sert à éviter.
   `safe_to_enforce` exige donc des données ET aucun échec.

 - **Ne pas confondre compteur absent et compteur à zéro.** `failed_sessions` n'est PAS
   `required` dans le profil de normalisation : un fournisseur peut envoyer un `summary`
   JSON valide avec `"total-failure-session-count": null` (clé présente, pas de
   `KeyError`), et la ligne est persistée avec `failed_sessions: null`. Si on lisait ça
   comme 0, une ligne qui dit littéralement « je ne sais pas combien de sessions ont
   échoué » serait comptée comme « aucune n'a échoué » — silence pris pour succès, mais
   au niveau du champ au lieu du domaine. On distingue donc les lignes `summary`
   complètes (les deux compteurs sont des entiers lisibles, zéro compris) des lignes
   incomplètes, et `safe_to_enforce` refuse `true` dès qu'il en existe une seule : on ne
   dit « c'est sûr » que si on a réellement tout lu.

 - **Ne pas jeter la moitié connue d'une ligne incomplète.** Un `summary` avec
   `successful_sessions: null` et `failed_sessions: 5` documente 5 échecs RÉELS ; que
   l'autre compteur soit illisible n'efface pas ces 5 échecs. Écarter la ligne entière
   (comme le faisait une version précédente) ferait disparaître un échec avéré de
   `sessions_failed` — le pire des deux mondes : un sous-comptage silencieux déguisé en
   prudence, précisément ce que ce module existe pour empêcher. On compte donc chaque
   moitié lisible indépendamment de l'autre ; `incomplete_rows` continue de signaler la
   ligne dès qu'un des deux compteurs manque, et `safe_to_enforce` reste bloqué — mais
   le chiffre affiché à l'écran, lui, ne cache jamais un échec qu'on connaît.

 - Ne pas supposer qu'une ligne `failure` en base implique toujours un `summary`
   coherent : `total`, `sessions_failed` et `incomplete_rows` viennent EXCLUSIVEMENT
   des lignes `summary`. Mais un `summary` peut echouer a se normaliser (compteur
   illisible -> TYPE_CAST -> ligne entiere rejetee par le normaliseur) alors que les
   lignes `failure` du meme rapport se normalisent tres bien et sont persistees. Sans
   verifier aussi `failures`, `safe_to_enforce` pourrait valoir `True` avec des echecs
   ecrits en base (voir le commentaire sur `not failures` plus bas). Ce n'est PAS un
   contournement du principe "le total vient toujours des lignes summary" : ce
   principe regit le calcul d'un NOMBRE (pas de double comptage), tandis que
   `safe_to_enforce` est un BOOLEEN distinct qui doit rester `False` des qu'un echec
   connu existe, peu importe par quelle ligne il a ete vu.

 - Ne pas supposer qu'un rapport rejeté laisse toujours une trace dans `report_row` :
   une politique dont le `policy-domain` est illisible ne laisse AUCUNE ligne du
   tout (`Report.status == "failed"`, zéro `ReportRow`). `sessions_failed`,
   `incomplete_rows` et `failures` sont alors tous à leur valeur la plus rassurante,
   alors que ce rapport pouvait porter des dizaines d'échecs. `reports_unreadable`
   compte ces rapports directement sur `Report.status`/`profile_id`, PAS sur
   `report_row` : c'est le seul endroit qui sait qu'un rapport est arrivé même quand
   son contenu n'a jamais atteint la table qu'interroge le reste de cette fonction.

 - Ne pas oublier la pièce jointe dont on n'a jamais su la NATURE. `_record_unreadable`
   (`app.workers.tasks`) crée un `Report(status="failed")` avec `profile_id` NULL --
   précisément parce qu'on n'a pas pu établir si c'était un rapport DMARC, un rapport
   TLS, ou autre chose (archive `.gz`/`.zip` tronquée, CRC cassé...). Un rapport
   DMARC ou TLS LISIBLE mais en échec porte, lui, toujours un `profile_id` : compter
   aussi les rapports `profile_id IS NULL` ne produit donc pas de rouge parasite. On
   assume que cela élargit la portée du panneau TLS à des pièces jointes qui
   n'étaient peut-être même pas du TLS -- c'est voulu : le silence d'une pièce
   jointe qu'on n'a jamais su identifier n'est jamais un « rien à signaler ».

 - Ne pas laisser un rejet DÉLIBÉRÉ, sur un fichier identifié avec certitude, devenir
   un levier de nuisance sur la décision `enforce`. La boîte de collecte est
   OUVERTE : n'importe qui peut envoyer un e-mail dont le sujet fait résoudre le
   tenant (« Report Domain: acme.com »). Deux codes `ParsingError` marquent un
   rejet de ce type -- PAS un rapport illisible, un fichier qu'on a identifié avec
   certitude et écarté à raison -- et sont exclus de `reports_unreadable` :
     * `DMARC_DOMAIN_MISMATCH` (`guard_report_domain`) : un rapport parfaitement
       LISIBLE, mais qui ne concerne pas ce tenant -- forgé ou mal aiguillé ;
     * `VIRUS_DETECTED` (`_record_infected`, `app.workers.tasks`) : un fichier
       identifié comme malveillant AVANT tout parsing. Ce chemin n'est même pas
       filtré par `looks_like_report` -- un simple `.png` infecté (un fichier
       EICAR suffit) le déclenche, ce qui en fait un levier de nuisance encore
       plus trivial que le garde anti-usurpation s'il n'était pas exclu.
   Exclure ces deux codes ferme DEUX leviers. **On dit ici, explicitement, ce qu'on
   ne ferme PAS** : il en reste un troisième, VOLONTAIREMENT ouvert.
   `_record_unreadable` (`ATTACHMENT_UNREADABLE`) trace une pièce jointe qui
   PRÉTENDAIT être un rapport (extension `.gz`/`.zip`/`.xml`/`.json`, ou aucune)
   mais qu'on n'a jamais pu identifier -- une archive tronquée ou au CRC cassé ne
   laisse paraître AUCUN `policy_domain`, donc AUCUN contrôle de domaine n'a pu
   s'exercer dessus. On ne peut PAS distinguer, après coup, un tel rapport tronqué
   venant d'un vrai fournisseur des ordures qu'un attaquant aurait forgées : les
   deux produisent exactement le même `Report(status="failed", profile_id=NULL)`,
   sans aucune trace qui permette de trancher. Un attaquant qui le sait peut donc
   joindre un `.gz` corrompu au sujet du domaine d'un client et forcer ce panneau
   au ROUGE pendant toute la fenêtre `days`.

   On l'ACCEPTE, en connaissance de cause, parce que les deux pannes n'ont pas le
   même prix :
     * un FAUX VERT fait perdre du courrier dès le passage en `enforce` --
       IRRÉVERSIBLE, et invisible de notre côté (l'expéditeur renonce en silence,
       sans alerte) ;
     * un FAUX ROUGE (l'attaquant force `reports_unreadable > 0`) ne fait que
       RETARDER le durcissement -- RÉVERSIBLE, sans aucune perte : le pire qu'il
       inflige, c'est de garder le tenant en mode `testing` un peu plus longtemps.
   Retirer `ATTACHMENT_UNREADABLE` du compte, comme on vient de le faire pour
   `DMARC_DOMAIN_MISMATCH` et `VIRUS_DETECTED`, rouvrirait exactement le FAUX VERT
   que ce module existe pour empêcher : un rapport tronqué venant d'un VRAI
   fournisseur (et portant peut-être de vrais échecs de certificat) redeviendrait
   invisible. On préfère donc, délibérément, un levier de nuisance réversible à
   une fuite de courrier irréversible. Ce module ne prétend PAS avoir fermé tous
   les leviers ouverts par une boîte de collecte ouverte -- seulement les deux qui
   pouvaient l'être sans rouvrir le faux vert.

Le service ne connaît pas le tenant : il reçoit une session **déjà scopée**. C'est ce qui
le rend testable seul et incapable de fuiter — aucun `WHERE tenant_id` applicatif, la RLS
fait le travail (CLAUDE.md).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import or_

from app.db.models import ParsingError, Report, ReportRow
from app.services.counters import int_or_none as _int_or_none

_kind = ReportRow.data["kind"].astext
_report_date = ReportRow.data["report_date"].astext

# Reconnaître un rapport TLS par MOTIF (`LIKE '%tlsrpt_json' ESCAPE '\'`), pas par
# égalité stricte sur `_default_tlsrpt_json`. `select_profile()` (voir
# `app.normalization.profiles`) sert un profil `{domaine}_tlsrpt_json` en PRIORITÉ
# quand un fichier `profiles/{domaine}_tlsrpt_json.json` existe pour ce tenant, et
# n'utilise `_default_tlsrpt_json` qu'en repli. Ajouter un tel profil spécifique est
# une opération de DONNÉE, sans code ni déploiement (CLAUDE.md) : le jour où
# quelqu'un dépose ce fichier pour un tenant, une égalité stricte cesserait de
# reconnaître ses rapports TLS, `reports_unreadable` retomberait silencieusement à
# 0 pour ce tenant, le faux feu vert reviendrait, et AUCUN test ne le verrait. Le
# suffixe `tlsrpt_json` est commun aux deux formes du nom de profil ; il est fixé
# par `@register("tlsrpt_json")` dans l'adaptateur (voir
# `app.parsing.adapters.tlsrpt_adapter`), pas par convention arbitraire ici.
#
# Le `_` entre `tlsrpt` et `json` est un caractère LITTÉRAL dans le nom de profil,
# mais `_` est aussi le joker LIKE « un caractère quelconque » : sans échappement,
# le motif matcherait aussi un hypothétique `tlsrptXjson`. Sans conséquence
# pratique ici -- aucun profil de ce nom n'existe, et l'élargissement irait de
# toute façon dans le sens du ROUGE (plus de profils reconnus comme TLS = plus de
# rapports en échec comptés), jamais du VERT -- mais le motif doit dire
# littéralement ce que ce commentaire prétend. D'où l'échappement explicite et le
# paramètre `escape` sur le `.like()` plus bas.
_TLS_PROFILE_PATTERN = r"%tlsrpt\_json"
_LIKE_ESCAPE = "\\"

# Codes posés par des gardes qui rejettent un fichier identifié AVEC CERTITUDE,
# délibérément -- pas un rapport illisible. La boîte de collecte est OUVERTE :
# n'importe qui peut forger un rapport TLS au sujet du domaine d'un client, ou lui
# faire subir une pièce jointe malveillante, pour le priver de son feu vert. Les
# compter dans `reports_unreadable` transformerait ces gardes en un levier de
# nuisance sur la DÉCISION enforce -- voir le commentaire de module pour
# l'arbitrage complet (deux leviers fermés, un troisième volontairement ouvert).
#
#  * `DMARC_DOMAIN_MISMATCH` (`guard_report_domain`, `app.parsing.guards`) : un
#    rapport parfaitement LISIBLE, mais qui ne concerne pas ce tenant. Le même
#    code sert pour DMARC et TLS-RPT, `guard_report_domain` ne distinguant pas
#    les deux formats (les deux déclarent `policy_domain`).
#  * `VIRUS_DETECTED` (`_record_infected`, `app.workers.tasks`) : un fichier
#    identifié comme malveillant par l'antivirus, AVANT tout parsing -- ce chemin
#    n'est même pas filtré par `looks_like_report`, un `.png` infecté (un simple
#    EICAR) le déclenche tout autant qu'une fausse archive TLS-RPT.
_CODES_ECARTES_A_JUSTE_TITRE = ("DMARC_DOMAIN_MISMATCH", "VIRUS_DETECTED")


def posture(db, days: int = 30) -> dict:
    cutoff = (date.today() - timedelta(days=days)).isoformat()

    rows = (db.query(ReportRow)
              .filter(_kind.in_(("summary", "failure")))
              .filter(_report_date >= cutoff)
              .all())

    # Un rapport TLS peut échouer à se normaliser AVANT qu'aucune ligne n'existe :
    # `TLSRPT_BAD_POLICY` (compteur de résumé illisible) peut faire tomber la seule
    # politique du rapport, qui fait à son tour tomber `policy_domain` ->
    # `TLSRPT_NO_POLICY_DOMAIN` -> `ParseResult(status="failed")`, ZÉRO ligne
    # persistée. Un tel rapport ne laisse absolument AUCUNE trace dans `report_row` :
    # ni `sessions_failed`, ni `incomplete_rows`, ni `failures` ne peuvent le voir,
    # puisque ces trois champs ne lisent QUE `report_row`. Il faut donc consulter
    # `Report.status` directement -- la seule table qui sait qu'un rapport est arrivé
    # même quand son contenu n'a jamais atteint `report_row`.
    #
    # Deuxième source, distincte de la première : `_record_parsing_failure`
    # (`app.workers.tasks`, appelée par `_record_unreadable`) crée un `Report` avec
    # `profile_id` NULL -- précisément parce qu'une pièce jointe (archive `.gz`/`.zip`
    # tronquée, CRC cassé...) n'a jamais pu être identifiée comme DMARC ou TLS avant
    # d'échouer. Un rapport DMARC ou TLS LISIBLE mais en échec porte, lui, TOUJOURS un
    # `profile_id` (posé avant le parsing par `select_profile()`) : il n'y a donc pas
    # de rouge parasite de ce côté. On assume et on le dit : une pièce jointe DMARC
    # illisible fait aussi passer ce panneau TLS au rouge -- c'est le sens du principe
    # du module, on ne prétend jamais savoir ce qu'on ignore. C'est pour ça que le
    # texte affiché à l'écran (`MtaStsPanel.tsx`) parle d'« une pièce jointe qui
    # prétendait être un rapport », jamais d'« un rapport TLS ».
    #
    # Dans les deux cas, on exclut les rapports rejetés à juste titre
    # (`_CODES_ECARTES_A_JUSTE_TITRE` : garde anti-usurpation ET antivirus) : ce ne
    # sont pas des rapports illisibles, ce sont des fichiers identifiés avec
    # certitude et écartés délibérément (voir le commentaire sur cette constante,
    # et celui de module pour l'arbitrage sur ce qui RESTE ouvert malgré cette
    # exclusion). Sans elle, un simple e-mail forgé -- ou une pièce jointe
    # infectée -- suffirait à retirer son feu vert à un tenant pendant toute la
    # fenêtre.
    #
    # `Report.status != "ok"` inclut aussi les rapports `partial` (des lignes ONT
    # été persistées, mais certaines lignes du même rapport ont été rejetées par le
    # normaliseur) -- pas seulement `failed` (zéro ligne persistée). Les deux
    # méritent d'être comptés : un rapport `partial` a, par définition, une partie
    # de son contenu qu'on n'a PAS su lire, exactement comme un rapport `failed` en
    # a la totalité. Mais les deux ne sont PAS équivalents, et le texte affiché à
    # l'écran (`MtaStsPanel.tsx`) se garde de prétendre le contraire : pour un
    # rapport `partial`, une partie du contenu a réellement atteint les compteurs
    # ci-dessus (`sessions_ok`/`sessions_failed`/`failures`) ; pour un rapport
    # `failed`, aucune ligne n'existe et rien n'y a jamais atteint.
    #
    # Fenêtre de temps : `Report.created_at` (date de RÉCEPTION/traitement chez nous),
    # PAS `report_date` (date du rapport côté fournisseur, un champ de `ReportRow.data`
    # qui n'existe tout simplement pas ici puisqu'aucune ligne n'a été persistée). Les
    # deux dates répondent à des questions différentes : `report_date` demande "quel
    # jour ce rapport décrit-il ?", `created_at` demande "quand avons-nous appris que
    # nous ne savions pas ?" -- c'est cette seconde question que ce garde pose, la
    # première n'a pas de réponse pour un rapport qui n'a jamais été normalisé.
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)
    _ecarte_a_juste_titre = (
        db.query(ParsingError.id)
          .filter(ParsingError.report_id == Report.id)
          .filter(ParsingError.code.in_(_CODES_ECARTES_A_JUSTE_TITRE))
          .exists()
    )
    reports_unreadable = (
        db.query(Report)
          .filter(or_(Report.profile_id.like(_TLS_PROFILE_PATTERN, escape=_LIKE_ESCAPE),
                      Report.profile_id.is_(None)))
          .filter(Report.status != "ok")
          .filter(Report.created_at >= cutoff_dt)
          .filter(~_ecarte_a_juste_titre)
          .count()
    )

    sessions_ok = 0
    sessions_failed = 0
    incomplete_rows = 0
    reporters: set[str] = set()

    # Un échec est identifié par (type, MTA émetteur, MX visé) : c'est ce triplet qui dit
    # à l'exploitant quoi corriger. Deux rapports différents décrivant le même problème
    # doivent s'additionner, pas se dupliquer.
    # Valeur : {"total": somme des `failure_sessions` LISIBLES du triplet, "has_known":
    # au moins une occurrence lisible (donc `total` est un vrai chiffre, pas un 0
    # fabriqué), "has_unknown": au moins une occurrence illisible}. Les deux booléens
    # sont indépendants — un triplet peut être *en partie* lisible — c'est précisément
    # ce que `has_known`/`has_unknown` distincts permettent de représenter, alors qu'un
    # simple `Counter` ne peut ni signaler « inconnu » sans un 0 mensonger, ni signaler
    # « connu MAIS partiel ». Voir `_int_or_none` plus bas et le commentaire sur
    # `failures`.
    detail: dict[tuple[str, str, str], dict] = {}

    for r in rows:
        d = r.data
        if d.get("reporter"):
            reporters.add(str(d["reporter"]))

        if d.get("kind") == "summary":
            ok = _int_or_none(d.get("successful_sessions"))
            failed = _int_or_none(d.get("failed_sessions"))
            if ok is None or failed is None:
                # Un des deux totaux est absent ou illisible : la ligne est incomplète,
                # donc `safe_to_enforce` ne pourra jamais dire « c'est sûr » pour cette
                # période. Mais jeter la ligne ENTIÈRE serait pire que de la garder à
                # moitié : si `failed_sessions` est lisible, il décrit un échec RÉEL et
                # CONNU, indépendamment du fait que `successful_sessions` le soit ou
                # non — l'écarter le ferait disparaître de `sessions_failed`, soit
                # exactement le sous-comptage silencieux que ce module doit empêcher.
                # On compte donc chaque moitié lisible, séparément, et on garde le
                # signal d'incertitude (voir le commentaire de module).
                incomplete_rows += 1
            if ok is not None:
                sessions_ok += ok
            if failed is not None:
                sessions_failed += failed
            continue

        key = (str(d.get("result_type") or "inconnu"),
               str(d.get("sending_mta_ip") or ""),
               str(d.get("receiving_mx_hostname") or ""))
        entry = detail.setdefault(key, {"total": 0, "has_known": False, "has_unknown": False})
        sessions = _int_or_none(d.get("failure_sessions"))
        if sessions is None:
            # Cette OCCURRENCE du triplet est illisible — mais d'autres occurrences du
            # même triplet (un autre fournisseur, un autre jour) peuvent très bien être
            # lisibles. On ne touche pas à `total` ici : le marquer illisible ne doit
            # PAS effacer ce que d'autres occurrences ont déjà additionné.
            entry["has_unknown"] = True
        else:
            entry["total"] += sessions
            entry["has_known"] = True

    # Arbitrage (même principe que pour les lignes `summary` ci-dessus, appliqué ici à
    # `failures`) : une version précédente affichait `sessions: None` dès qu'UNE seule
    # occurrence du triplet était illisible — même si d'autres occurrences avaient un
    # nombre parfaitement lisible. Exemple réel : Google chiffre 3 sessions sur
    # (certificate-expired, 203.0.113.5, mx.exemple.fr), Microsoft décrit le MÊME
    # triplet sans nombre exploitable. Le total interne CONNU vaut 3 ; le cacher
    # derrière un « inconnu » est tout aussi trompeur que le faux zéro qu'on a corrigé
    # pour `summary` — un mensonge par ignorance feinte au lieu d'un mensonge par
    # défaut. On affiche donc ce qu'on sait (`sessions` = somme des occurrences
    # lisibles, `None` seulement si AUCUNE ne l'était) et on DIT que c'est un minorant
    # via `partial` : `true` signifie « au moins 3, peut-être plus, une source au
    # moins n'a rien pu chiffrer ». Au frontend de traduire ça en « au moins 3
    # sessions » plutôt que de choisir entre un chiffre faux et un silence feint.
    failures_unsorted = [
        {"result_type": rt,
         "sessions": v["total"] if v["has_known"] else None,
         "partial": v["has_unknown"],
         "sending_mta_ip": ip or None, "receiving_mx_hostname": mx or None}
        for (rt, ip, mx), v in detail.items()
    ]

    # Tri par magnitude décroissante — SAUF que la magnitude inconnue (`sessions: None`)
    # ne va jamais en bas de liste : elle n'est pas « la moins grave », elle est juste
    # « pas mesurée ». La reléguer en fin de liste la ferait passer, à l'écran, pour
    # anodine. On la fait donc remonter en tête, avant tout total connu.
    failures = sorted(failures_unsorted,
                       key=lambda f: (f["sessions"] is not None, -(f["sessions"] or 0)))

    total = sessions_ok + sessions_failed

    return {
        "days": days,
        "sessions_total": total,
        "sessions_ok": sessions_ok,
        "sessions_failed": sessions_failed,
        "failures": failures,
        # Nombre de lignes `summary` dont on n'a PAS pu lire les deux totaux (compteur
        # absent ou non entier). Exposé tel quel : c'est à l'appelant (l'écran enforce)
        # de savoir qu'il existe des lignes muettes, pas seulement des échecs à zéro.
        "incomplete_rows": incomplete_rows,
        # Nombre de RAPPORTS (pas de lignes) dont tout ou partie du contenu n'a PAS pu
        # être lu -- `Report.status != "ok"` sur le profil TLS (par MOTIF, voir
        # `_TLS_PROFILE_PATTERN`) OU sur une pièce jointe jamais identifiée
        # (`profile_id IS NULL`, voir plus haut), à l'exclusion des rejets délibérés
        # (`_CODES_ECARTES_A_JUSTE_TITRE` : garde anti-usurpation ET antivirus -- voir
        # le commentaire de module pour ce que cette exclusion ferme, et ce qu'elle
        # laisse volontairement ouvert), dans la fenêtre. `status="failed"` : AUCUNE
        # ligne n'a jamais atteint `report_row`, rien de ce rapport n'apparaît ailleurs
        # dans ce dict. `status="partial"` : au contraire, une partie de ses lignes a
        # bien été persistée et compte déjà dans `sessions_ok`/`sessions_failed`/
        # `failures` -- seule la partie rejetée par le normaliseur est invisible
        # ailleurs. On compte les deux ici, mais on ne prétend jamais que « partial »
        # veut dire « rien de son contenu n'a compté » (voir `MtaStsPanel.tsx`, qui
        # distingue explicitement les deux dans son texte). Distinct d'`incomplete_rows` :
        # celui-ci compte des lignes `summary` PARTIELLEMENT lisibles (un compteur sur
        # deux) ; celui-là compte des RAPPORTS, `failed` ou `partial`, dont une partie du
        # contenu ne s'est jamais retrouvée dans les chiffres ci-dessus. « Je n'ai pas su
        # tout te lire » ne doit jamais se lire « rien à signaler » -- d'où un compteur
        # séparé plutôt qu'un 0 silencieux fondu dans les autres champs.
        "reports_unreadable": reports_unreadable,
        # Des données, aucun échec, ET rien d'illisible -- ni au niveau de la ligne
        # (compteur absent, `incomplete_rows`), ni au niveau du RAPPORT ENTIER
        # (`reports_unreadable`). Le silence n'est pas une preuve — ni au niveau du
        # domaine (aucun rapport), ni au niveau du champ (un compteur absent dans un
        # rapport reçu), ni au niveau du rapport (un rapport reçu mais jamais
        # normalisé). Une seule ligne incomplète, ou un seul rapport illisible,
        # suffit à refuser : on ne dit « c'est sûr » que si on a réellement TOUT lu.
        #
        # `not failures` en plus : PAS un double comptage (`safe_to_enforce` est un
        # booleen, pas une somme -- le total, lui, continue de venir exclusivement des
        # lignes `summary`, voir le commentaire de module). C'est un garde-fou
        # separe : une ligne `summary` peut echouer a se normaliser (compteur
        # illisible -> TYPE_CAST -> ligne rejetee par le normaliseur) pendant que les
        # lignes `failure` du meme rapport, elles, se normalisent et sont persistees.
        # Sans ce garde, `sessions_failed` et `incomplete_rows` resteraient a 0 (la
        # ligne muette n'arrive jamais en base) alors que `failures` decrit des echecs
        # reels : feu vert errone. Ne retire jamais ce garde au nom du "on compte deja
        # les echecs ailleurs" -- ce n'est justement pas la meme chose que compter.
        #
        # `reports_unreadable == 0` en plus : le garde ci-dessus voit un rapport
        # DEGRADE (des lignes en base, mais partiellement lisibles) ; il ne voit PAS
        # un rapport qui n'a laissé AUCUNE ligne du tout (policy-domain illisible ->
        # rapport rejeté en bloc avant normalisation). Sans ce second garde, un
        # rapport entier peut disparaître -- avec ses échecs dedans -- sans que rien
        # dans `report_row` ne le signale : `sessions_failed`, `incomplete_rows` et
        # `failures` resteraient tous à leur valeur la plus rassurante.
        "safe_to_enforce": (total > 0 and sessions_failed == 0
                            and incomplete_rows == 0 and not failures
                            and reports_unreadable == 0),
        "reporters": sorted(reporters),
    }

# `_int_or_none` (l'alias importe ci-dessus) vit maintenant dans
# `app.services.counters` : partage avec `ip_intel._activite`, qui applique la meme
# regle aux memes compteurs (voir le docstring de ce module).
