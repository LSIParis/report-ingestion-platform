from __future__ import annotations

import email as email_lib
from datetime import datetime, timezone
from email.message import Message

import structlog

from app.celery_app import celery
from app.config import settings
from app.db.models import Alert, Attachment, Email, ParsingError, Report, ReportRow, Tenant
from app.db.session import get_session, tenant_scoped_session
from app.normalization.normalizer import NormalizationService
from app.normalization.profiles import load_profile, select_profile
from app.parsing.base import ParseResult
from app.parsing.detect import detect_format, looks_like_report
from app.parsing.guards import guard_report_domain
from app.parsing.registry import get_adapter
from app.persistence.service import PersistenceService
from app.services import antivirus
from app.services.alerting import webhook
from app.services.alerting.reconciler import reconcile
from app.services.antivirus import AntivirusUnavailable
from app.services.audit import audit
from app.storage import ObjectStore
from app.tenant_resolver.resolver import TenantResolverService

# Enregistre les adaptateurs dans le registre
import app.parsing.adapters  # noqa: F401

log = structlog.get_logger()
store = ObjectStore.from_settings(settings)


class TransientError(Exception):
    """Erreur récupérable (S3/DB timeout…) → retry."""


@celery.task(bind=True, max_retries=4, default_retry_delay=30, acks_late=True)
def process_email(self, email_id: str) -> None:
    logger = log.bind(email_id=email_id)
    try:
        match = TenantResolverService().resolve(email_id)
        if not match.tenant_id:
            audit(actor="system", action="email.quarantined", target_id=email_id)
            return

        _set_status(email_id, "processing")
        audit(actor="system", action="email.tenant_resolved", target_id=email_id,
              tenant_id=match.tenant_id, metadata={"method": match.method})

        sources, infected, unreadable = _list_sources(email_id, match.tenant_id)
        if not sources and not infected and not unreadable:
            _set_status(email_id, "failed")
            audit(actor="system", action="email.failed", target_id=email_id,
                  tenant_id=match.tenant_id, metadata={"error": "no source"})
            return

        statuses = [_process_source(email_id, match.tenant_id, s) for s in sources]
        statuses += ["failed"] * infected      # chaque PJ infectée compte comme un échec
        statuses += ["failed"] * unreadable    # idem pour une PJ illisible (déjà tracée)
        final = _aggregate(statuses)
        _set_status(email_id, final)

        # Les alertes TLS apparaissent alors en minutes, pas le lendemain. C'est gratuit :
        # le réconciliateur est idempotent. Et ça ne casse JAMAIS le flux — un e-mail bien
        # traité reste bien traité, même si l'alerting tombe.
        try:
            reconcile_tenant(match.tenant_id)
        except Exception:  # noqa: BLE001
            log.exception("alerting.reconciliation_en_echec", email_id=email_id)

        audit(actor="system", action="email.processed", target_id=email_id,
              tenant_id=match.tenant_id, metadata={"result": final})

    except (TransientError, AntivirusUnavailable) as exc:
        logger.warning("process.transient", error=str(exc))
        raise self.retry(exc=exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("process.fatal")
        _set_status(email_id, "failed")
        audit(actor="system", action="email.failed", target_id=email_id,
              metadata={"error": str(exc)})
        raise


@celery.task
def reprocess_report(email_id: str) -> None:
    """Reprise manuelle depuis le brut S3 — sans re-recevoir le mail. Idempotent."""
    _cleanup_previous(email_id)
    process_email.delay(email_id)


# ---------------- alertes ----------------
@celery.task
def sweep_alerts() -> None:
    """Balayage quotidien de tous les domaines actifs.

    Un ordonnanceur est INDISPENSABLE ici, et c'est contre-intuitif : un échec TLS arrive
    avec un e-mail, donc le worker tourne déjà. Mais un domaine SILENCIEUX ne produit aucun
    événement — c'est sa définition même. On ne peut pas réagir à ce qui n'arrive pas.
    """
    with get_session() as db:      # plan système : juste pour ÉNUMÉRER les domaines
        tenant_ids = [str(t.id) for t in
                      db.query(Tenant).filter_by(status="active").all()]

    for tenant_id in tenant_ids:
        try:
            reconcile_tenant(tenant_id)
        except Exception:  # noqa: BLE001 — un domaine en échec ne prive pas les autres
            log.exception("alerting.balayage_en_echec", tenant_id=tenant_id)


def reconcile_tenant(tenant_id: str) -> None:
    """Réconcilie les alertes d'UN domaine, puis notifie.

    ⚠️ SESSION SCOPÉE, SANS BYPASS — et c'est tout l'enjeu de cette fonction.

    Les détecteurs n'ont AUCUN filtre `tenant_id` applicatif : ils comptent sur la RLS
    (CLAUDE.md). Leur passer la session du worker (`get_session()`, qui a BYPASSRLS) leur
    ferait voir TOUS les tenants — et ouvrirait les alertes d'un client sur le domaine d'un
    autre. Avec un seul tenant en développement, ce bug est INVISIBLE.
    """
    evenements: list[tuple[str, str]] = []

    with tenant_scoped_session(tenant_id=tenant_id) as db:
        tenant = db.get(Tenant, tenant_id)
        if not tenant or tenant.status != "active":
            return
        res = reconcile(db, tenant)

        # Les ids déjà retenus CE cycle -- sert à ne pas les reprendre deux fois via le
        # rattrapage ci-dessous (une alerte fraîchement ouverte/fermée a, par
        # construction, ses colonnes *_notified_at encore NULL au moment où on regarde).
        deja_pris = ({str(a.id) for a in res.closed} | {str(a.id) for a in res.opened})

        # --- Rattrapage des notifications perdues -----------------------------------
        # Les colonnes `opened_notified_at` / `closed_notified_at` (migration 0008)
        # jouaient jusqu'ici UN rôle : garde d'idempotence contre le rejeu Celery
        # (`task_acks_late=True`). Elles en jouent maintenant un SECOND, tout aussi
        # nécessaire : filet de rattrapage. Trois chemins font qu'une alerte s'ouvre en
        # base SANS que personne ne soit jamais prévenu -- et aucun n'est rattrapé par
        # le réconciliateur seul :
        #   - `notify_alerts.delay(...)` lève (Redis indisponible) -> journalisé, puis
        #     abandonné ;
        #   - les retries Celery sont épuisés (webhook durablement en panne) -> la
        #     tâche meurt ;
        #   - le worker est tué entre le commit de l'alerte et l'enfilement de sa
        #     notification -> aucune tâche n'a jamais existé.
        # Le cycle suivant ne répare rien tout seul : `reconcile()` voit la condition
        # déjà ouverte et passe son chemin (`continue`), l'alerte ne réapparaît plus
        # JAMAIS dans `res.opened`. Sans ce filet, une alerte ouverte que personne ne
        # voit jamais est exactement la panne que ce système existe pour combattre.
        # Sans risque de doublon : `notify_alerts` (plus bas) est gardée par ces mêmes
        # colonnes, donc redemander une notification déjà envoyée est un no-op.
        fermetures_manquees = (
            db.query(Alert)
            .filter(Alert.closed_at.isnot(None), Alert.closed_notified_at.is_(None))
            .all()
        )
        ouvertures_manquees = (
            db.query(Alert)
            .filter(Alert.closed_at.is_(None), Alert.opened_notified_at.is_(None))
            .all()
        )

        # Fermetures AVANT ouvertures, y compris pour le rattrapage : un changement de
        # sévérité (voir reconciler.py) ferme l'ancienne alerte et en ouvre une neuve
        # DANS LE MÊME CYCLE -- même kind, même dedup_key, juste une sévérité
        # différente. Si on notifiait l'ouverture en premier, un consommateur naïf du
        # webhook (n8n, un script) qui suit une alerte par (kind, dedup_key) verrait
        # « ouverte (critical) » PUIS « fermée (warning) », et conclurait à tort que le
        # problème est résolu -- alors qu'il vient de s'aggraver et que du courrier est
        # refusé. Fermer d'abord dit l'aggravation dans le bon ordre. Les ids doivent
        # être lus AVANT la fermeture de la session.
        evenements = (
            [("closed", str(a.id)) for a in res.closed]
            + [("closed", str(a.id)) for a in fermetures_manquees
               if str(a.id) not in deja_pris]
            + [("opened", str(a.id)) for a in res.opened]
            + [("opened", str(a.id)) for a in ouvertures_manquees
               if str(a.id) not in deja_pris]
        )

    # Notifier APRÈS le commit : on ne notifie que ce qui est réellement en base. UNE
    # SEULE tâche Celery pour tout le cycle (voir `notify_alerts`) -- Celery ne garantit
    # l'ordre qu'à l'ENFILEMENT, jamais à la LIVRAISON : deux `.delay()` indépendants
    # peuvent s'exécuter en parallèle (worker prefork) ou être inversés par un retry, ce
    # qui inverserait GARANTIE l'ordre fermeture/ouverture ci-dessus si on les enfilait
    # séparément.
    if evenements:
        try:
            notify_alerts.delay(evenements)
        except Exception:  # noqa: BLE001 — le canal ne casse JAMAIS le flux métier
            log.exception("alerting.notification_non_planifiee", evenements=evenements)


@celery.task(bind=True, max_retries=5, default_retry_delay=60)
def notify_alerts(self, events: list[tuple[str, str]]) -> None:
    """Envoie TOUS les événements d'un cycle de réconciliation, dans l'ordre, DANS UNE
    SEULE tâche Celery.

    Pourquoi une seule tâche et pas N tâches indépendantes : l'ordre « fermeture avant
    ouverture » n'est garanti qu'à l'ENFILEMENT (`reconcile_tenant` enfile bien dans le
    bon ordre), jamais à la LIVRAISON. Deux tâches Celery indépendantes dans la même
    file peuvent être exécutées en parallèle par un worker prefork (plusieurs
    processus), et si la fermeture échoue et part en retry (60 s plus tard) pendant que
    l'ouverture est déjà partie, l'inversion est GARANTIE. Un consommateur du webhook
    (n8n, un script) qui suit une alerte par (kind, dedup_key) verrait alors « ouverte
    (critical) » PUIS « fermée (warning) » et conclurait à tort que le problème est
    résolu -- alors qu'il vient de s'aggraver. Traiter la liste en séquence, à
    l'intérieur d'une seule tâche, élimine la course : aucune tâche indépendante ne peut
    en doubler une autre.

    On s'ARRÊTE au premier échec (`WebhookIndisponible`) plutôt que de continuer sur les
    événements suivants : continuer ferait exactement repartir le bug qu'on corrige (une
    ouverture partirait avant qu'une fermeture, retentée plus tard, n'ait pu partir).
    Toute la tâche est retentée par Celery, reprenant la liste depuis le début.

    Cette tâche reste retentable SANS DANGER : chaque paire (event, alert_id) est
    protégée par sa propre garde d'idempotence (`opened_notified_at` /
    `closed_notified_at`, déjà en base). Si la tâche est retentée après avoir notifié
    les 3 premiers événements sur 5, les 3 premiers sont des no-op (déjà notifiés) et
    seuls les 2 restants repartent réellement sur le webhook -- rejouer la tâche
    ENTIÈRE est donc sans danger. Vérifié par
    `test_rejouer_notify_alerts_ne_renvoie_pas_ce_qui_est_deja_notifie`.
    """
    with get_session() as db:            # tâche système : lecture cross-tenant assumée
        for event, alert_id in events:
            try:
                _notifier_un_evenement(db, event, alert_id)
            except webhook.WebhookIndisponible as exc:
                raise self.retry(exc=exc)  # noqa: B904 — retry Celery


def _notifier_un_evenement(db, event: str, alert_id: str) -> None:
    """Traite UN événement (garde d'idempotence + POST + marquage). Appelé en séquence
    par `notify_alerts` pour chaque événement d'un cycle -- factorisé pour que la boucle
    ci-dessus reste lisible et que l'arrêt au premier échec soit explicite.

    Idempotence RÉELLE mais PAS STRICTE : la garde est vérifiée AVANT le POST, et la
    colonne *_notified_at n'est commit qu'APRÈS. Un worker tué entre les deux renverra
    donc la même notification au rejeu -- un doublon, jamais une perte. C'est un choix
    assumé (voir `webhook.py`) : un doublon sur un webhook coûte moins cher qu'une
    alerte jamais vue.
    """
    alerte = db.get(Alert, alert_id)
    if not alerte:
        return
    tenant = db.get(Tenant, alerte.tenant_id)
    if not tenant:
        # Le tenant peut avoir disparu entre l'ouverture de l'alerte et l'envoi de
        # sa notification (tâche différée, ou rejouée bien plus tard) -- garde
        # symétrique à celle du dessus pour l'alerte introuvable : on abandonne
        # silencieusement, on ne casse jamais la tâche Celery pour ça.
        return

    # Explicite plutôt qu'un `else` fourre-tout : un troisième type d'événement, un
    # jour, doit lever bruyamment plutôt que s'écrire en silence dans
    # `closed_notified_at` (c'est ce que faisait l'ancien `else`).
    if event == "opened":
        deja_notifiee = alerte.opened_notified_at
    elif event == "closed":
        deja_notifiee = alerte.closed_notified_at
    else:
        raise ValueError(f"événement d'alerte inconnu : {event!r}")

    # Idempotence : `task_acks_late=True` (app/celery_app.py) => livraison Celery
    # AT-LEAST-ONCE. Un worker tué après l'envoi mais avant l'acquittement REJOUE la
    # tâche -- sans garde, la même notification repartirait deux fois sur le
    # webhook. Une alerte est notifiée deux fois LÉGITIMEMENT dans sa vie (à son
    # ouverture, puis à sa fermeture) : une seule colonne ne peut pas distinguer les
    # deux, d'où deux colonnes dédiées (migration 0008), chacune la garde de SON
    # événement.
    if deja_notifiee is not None:
        return

    # `envoyer()` peut lever `WebhookIndisponible` (canal CONFIGURÉ mais EN PANNE) : dans
    # ce cas, l'exception remonte à travers cette fonction AVANT d'atteindre les lignes
    # suivantes -- la colonne *_notified_at reste NULL et l'événement repart bien au
    # prochain balayage. C'est le seul cas qu'on retente.
    #
    # `envoyer()` renvoie False dans un seul autre cas : `ALERT_WEBHOOK_URL` n'est pas
    # configurée (voir `webhook.envoyer`). Ce n'est PAS une panne : « pas de canal
    # configuré » est un état STABLE, déjà journalisé (dans `webhook.envoyer`) -- inutile
    # de le reconstater à chaque cycle. On pose donc la colonne *_notified_at qu'on ait
    # RÉELLEMENT envoyé, ou délibérément PAS envoyé faute de canal : c'est précisément ce
    # qui sort l'événement du filet de rattrapage (`fermetures_manquees` /
    # `ouvertures_manquees` dans `reconcile_tenant`). Sans cette distinction, ce filet
    # reprendrait la MÊME alerte à CHAQUE balayage -- un batch qui grossit sans borne sur
    # une plateforme qui n'a jamais configuré de webhook (le cas par défaut).
    webhook.envoyer(event, alerte, tenant)
    now = datetime.now(timezone.utc)
    if event == "opened":
        alerte.opened_notified_at = now
    else:
        alerte.closed_notified_at = now
    db.commit()


# ---------------- helpers ----------------
def _process_source(email_id: str, tenant_id: str, source: dict) -> str:
    profile_id = select_profile(tenant_id, source["fmt"], source.get("filename"))
    try:
        raw = store.get_default(source["object_key"])
        profile = load_profile(profile_id)
        parsed = get_adapter(source["fmt"]).parse(raw, profile)
        parsed = guard_report_domain(parsed, _tenant_domain(tenant_id))
        normalized = NormalizationService().normalize(parsed, profile)
    except FileNotFoundError:
        normalized = ParseResult(status="failed",
                                 errors=[{"code": "PROFILE_NOT_FOUND",
                                          "message": f"Profil '{profile_id}' introuvable",
                                          "severity": "fatal"}])
    except LookupError as exc:
        normalized = ParseResult(status="failed",
                                 errors=[{"code": "NO_ADAPTER", "message": str(exc),
                                          "severity": "fatal"}])

    PersistenceService().persist(
        tenant_id=tenant_id, email_id=email_id,
        attachment_id=source.get("attachment_id"),
        profile_id=profile_id, source_type=source["type"], result=normalized,
    )
    return normalized.status


def _tenant_domain(tenant_id: str) -> str | None:
    with get_session() as db:
        t = db.get(Tenant, tenant_id)
        return t.domain if t else None


def _list_sources(email_id: str, tenant_id: str) -> tuple[list[dict], int, int]:
    """Relit le .eml brut, SCANNE (antivirus) puis extrait les pièces jointes vers
    S3 + rows Attachment. Renvoie (sources parsables, nb de PJ infectées, nb de PJ
    illisibles mais tracées)."""
    with get_session() as db:
        em = db.get(Email, email_id)
        raw_eml = store.get_default(em.raw_object_key)

    msg: Message = email_lib.message_from_bytes(raw_eml)
    sources: list[dict] = []
    infected = 0
    unreadable = 0

    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = part.get_filename()
        if not filename:
            continue
        payload = part.get_payload(decode=True) or b""

        # --- Antivirus AVANT tout stockage/parsing ---
        # `detect_format` DÉCOMPRESSE (gzip/zip) pour renifler le contenu : un scan
        # AVANT cette décompression est donc non négociable — décompresser un flux
        # hostile non scanné est une forme de parsing (invariant CLAUDE.md).
        # VirusFound → on trace et on saute (jamais stocké, jamais décompressé).
        # AntivirusUnavailable se propage → retry du worker (fail-safe).
        try:
            antivirus.scan(payload)
        except antivirus.VirusFound as v:
            _record_infected(email_id, tenant_id, filename,
                             part.get_content_type(), v.signature)
            infected += 1
            continue

        # Le CONTENU décide, pas le nom : `…json.gz` est un rapport TLS, pas du DMARC.
        fmt = detect_format(payload, filename)
        if not fmt:
            if looks_like_report(filename):
                # Cette pièce PRÉTEND être un rapport (extension .gz/.zip/.xml/.json,
                # ou aucune) mais on n'a pas su la lire (archive corrompue, tronquée…) :
                # ce n'est pas un fichier hors-sujet à ignorer, c'est une anomalie à
                # tracer — sinon un rapport qui n'arrive jamais disparaît en silence.
                _record_unreadable(email_id, tenant_id, filename,
                                   part.get_content_type(), payload)
                unreadable += 1
            continue  # extension hors-sujet (.txt, .png…) → ignorée en silence

        object_key = f"attachments/{email_id}/{filename}"
        store.put(object_key, payload,
                  content_type=part.get_content_type() or "application/octet-stream")

        with get_session() as db:
            att = Attachment(tenant_id=tenant_id, email_id=email_id, filename=filename,
                             mime_type=part.get_content_type(), format=fmt,
                             object_key=object_key, size_bytes=len(payload))
            db.add(att)
            db.flush()
            attachment_id = str(att.id)
            db.commit()

        sources.append({"type": "attachment", "fmt": fmt, "object_key": object_key,
                        "attachment_id": attachment_id, "filename": filename})

    return sources, infected, unreadable


def _record_parsing_failure(*, email_id: str, tenant_id: str, filename: str,
                            mime: str | None, payload: bytes | None,
                            code: str, message: str, context: dict,
                            action: str, metadata: dict) -> None:
    """Trace une PJ qu'on n'a pas pu exploiter : Attachment + Report(status=failed) +
    ParsingError + commit + audit -- factorise ce que `_record_infected` et
    `_record_unreadable` dupliquaient (~20 lignes identiques a l'exception du
    code/message et d'un choix). `payload=None` -> le fichier n'est PAS ecrit dans
    l'object store (PJ infectee : jamais stockee, meme pas pour la reparser un
    jour). `payload` fourni -> il est stocke normalement (PJ illisible, mais pas
    dangereuse)."""
    if payload is None:
        object_key = "(infecté — non stocké)"
        size_bytes = None
    else:
        object_key = f"attachments/{email_id}/{filename}"
        store.put(object_key, payload, content_type=mime or "application/octet-stream")
        size_bytes = len(payload)

    with get_session() as db:
        att = Attachment(tenant_id=tenant_id, email_id=email_id, filename=filename,
                         mime_type=mime, format=None,
                         object_key=object_key, size_bytes=size_bytes)
        db.add(att)
        db.flush()
        rep = Report(tenant_id=tenant_id, email_id=email_id, attachment_id=att.id,
                     source_type="attachment", status="failed", row_count=0)
        db.add(rep)
        db.flush()
        db.add(ParsingError(tenant_id=tenant_id, email_id=email_id, report_id=rep.id,
                            severity="fatal", code=code, message=message,
                            context=context))
        db.commit()
    audit(actor="system", action=action, target_id=email_id, tenant_id=tenant_id,
          metadata=metadata)


def _record_infected(email_id: str, tenant_id: str, filename: str,
                     mime: str | None, signature: str) -> None:
    """Trace une PJ infectée : Attachment (NON stockée) + Report échec +
    parsing_error VIRUS_DETECTED → visible dans le dashboard. Le fichier
    malveillant n'est jamais écrit dans l'object store."""
    _record_parsing_failure(
        email_id=email_id, tenant_id=tenant_id, filename=filename, mime=mime,
        payload=None, code="VIRUS_DETECTED",
        message=f"Pièce jointe infectée : {signature}",
        context={"filename": filename, "signature": signature},
        action="attachment.infected",
        metadata={"filename": filename, "signature": signature})


def _record_unreadable(email_id: str, tenant_id: str, filename: str,
                       mime: str | None, payload: bytes) -> None:
    """Trace une PJ qui RESSEMBLE à un rapport (extension .gz/.zip/.xml/.json, ou
    aucune) mais qu'on n'a pas su décoder : Attachment stockée (elle n'est PAS
    dangereuse, contrairement à un virus) + Report échec + parsing_error
    ATTACHMENT_UNREADABLE — visible dans le dashboard. Calqué sur `_record_infected`."""
    _record_parsing_failure(
        email_id=email_id, tenant_id=tenant_id, filename=filename, mime=mime,
        payload=payload, code="ATTACHMENT_UNREADABLE",
        message=f"Pièce jointe illisible : {filename}",
        context={"filename": filename},
        action="attachment.unreadable",
        metadata={"filename": filename})


def _set_status(email_id: str, status: str) -> None:
    with get_session() as db:
        em = db.get(Email, email_id)
        if em:
            em.status = status
            db.commit()


def _aggregate(statuses: list[str]) -> str:
    if not statuses:
        return "failed"
    if all(s == "ok" for s in statuses):
        return "parsed_ok"
    if all(s == "failed" for s in statuses):
        return "failed"
    return "parsed_partial"


def _cleanup_previous(email_id: str) -> None:
    """Supprime report/rows/errors ET attachments du run précédent (plan worker,
    cross-tenant) — pour que la reprise re-scanne et re-parse à neuf."""
    with get_session() as db:
        report_ids = [r.id for r in db.query(Report.id).filter_by(email_id=email_id).all()]
        if report_ids:
            db.query(ReportRow).filter(ReportRow.report_id.in_(report_ids)).delete(
                synchronize_session=False)
            db.query(ParsingError).filter(ParsingError.report_id.in_(report_ids)).delete(
                synchronize_session=False)
            db.query(Report).filter(Report.id.in_(report_ids)).delete(
                synchronize_session=False)
        # attachments recréés au re-parsing (report supprimé d'abord → pas de FK)
        db.query(Attachment).filter(Attachment.email_id == email_id).delete(
            synchronize_session=False)
        db.commit()
