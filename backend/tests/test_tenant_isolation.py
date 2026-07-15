"""Test d'isolation cross-tenant. DOIT passer — bloquant en CI.
Valide que la RLS (plan app_api) empêche tout accès inter-tenant, en lecture ET écriture.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import Alert, Email, ParsingError, Report, Tenant
from app.db.session import get_session, tenant_scoped_session
from app.workers import tasks


def test_tenant_a_cannot_read_tenant_b(seed_two_tenants):
    tid_a, tid_b = seed_two_tenants
    with tenant_scoped_session(tenant_id=tid_a) as db:
        rows = db.query(Report).all()
        assert all(str(r.tenant_id) == tid_a for r in rows)
        assert not any(str(r.tenant_id) == tid_b for r in rows)


def test_forged_insert_for_other_tenant_is_rejected(seed_two_tenants):
    tid_a, tid_b = seed_two_tenants
    with tenant_scoped_session(tenant_id=tid_a) as db:
        # email_id valide côté A pour satisfaire la FK ; tenant_id estampillé B (interdit).
        email_a = db.query(Email).first()
        db.add(Report(tenant_id=tid_b, email_id=email_a.id, source_type="body", status="ok", kind="dmarc"))
        with pytest.raises(Exception):
            db.flush()  # WITH CHECK doit rejeter l'écriture cross-tenant
        # Après l'erreur attendue, la transaction est en échec : on la nettoie pour
        # que le context manager puisse se refermer proprement.
        db.rollback()


def test_forged_alert_insert_for_other_tenant_is_rejected(seed_two_tenants):
    """Meme preuve que pour Report, mais pour `alert` (migration 0007) : une alerte
    estampillee du tenant B, ecrite depuis une session scopee sur le tenant A, doit
    etre rejetee -- pas seulement la lecture (USING), aussi l'ecriture forgee.

    Verifie en direct (ALTER POLICY temporaire sur la base de dev, voir le rapport de
    tache) : neutraliser SEULEMENT le WITH CHECK (le mettre a `true`) ne fait PAS
    echouer ce test -- l'INSERT ORM demande un RETURNING (a cause de `opened_at`,
    server_default), et Postgres applique alors aussi le USING sur la ligne retournee ;
    l'ecriture cross-tenant est donc rejetee par ce filet de securite meme si le WITH
    CHECK est casse. Le test ne devient rouge que si USING **et** WITH CHECK sont
    neutralises ensemble (policy entierement cassee) -- confirme en direct. Ce test
    protege donc contre une regression sur la policy dans son ensemble ; il ne peut pas
    a lui seul distinguer laquelle des deux clauses a failli.
    """
    tid_a, tid_b = seed_two_tenants
    with tenant_scoped_session(tenant_id=tid_a) as db:
        db.add(Alert(tenant_id=tid_b, kind="silent_domain", severity="critical"))
        with pytest.raises(Exception):
            db.flush()  # la policy (USING+WITH CHECK) doit rejeter l'ecriture cross-tenant
        # Apres l'erreur attendue, la transaction est en echec : on la nettoie pour
        # que le context manager puisse se refermer proprement.
        db.rollback()


def test_worker_sees_all_tenants(seed_two_tenants):
    tid_a, tid_b = seed_two_tenants
    with get_session() as db:  # plan système, BYPASSRLS
        seen = {str(r.tenant_id) for r in db.query(Report).all()}
        assert {tid_a, tid_b} <= seen


def test_ip_vue_par_b_est_invisible_de_a(seed_two_tenants):
    """Le cache ip_intel n'a pas de tenant_id. Ce qui empêche A de sonder l'existence
    d'une IP chez B, c'est le contrôle d'appartenance de la route : la requête ne trouve
    la ligne que si elle est visible SOUS RLS.

    Ce test valide la brique sur laquelle ce contrôle repose. S'il tombe, la route peut
    devenir un oracle : « cette IP est-elle dans votre cache ? » révélerait le trafic
    d'un autre client.
    """
    from app.db.models import ReportRow

    tid_a, tid_b = seed_two_tenants

    with get_session() as db:                       # plan worker : on sème chez B
        rep_b = db.query(Report).filter_by(tenant_id=tid_b).first()
        db.add(ReportRow(tenant_id=tid_b, report_id=rep_b.id,
                         data={"source_ip": "198.51.100.42", "message_count": 5}))
        db.commit()

    try:
        with tenant_scoped_session(tenant_id=tid_a) as db:   # A cherche l'IP de B
            vues = (db.query(ReportRow)
                      .filter(ReportRow.data["source_ip"].astext == "198.51.100.42")
                      .all())
            assert vues == [], "A voit une ligne de B : la route deviendrait un oracle"
    finally:
        with get_session() as db:
            db.query(ReportRow).filter(
                ReportRow.data["source_ip"].astext == "198.51.100.42").delete(
                synchronize_session=False)
            db.commit()


def test_tls_posture_tenant_a_ne_voit_rien_de_b(seed_two_tenants):
    """`tls_posture.posture()` alimente l'ecran qui autorise le passage de MTA-STS en
    `enforce` -- une lecture agregee de report_row, comme les autres, doit rester
    filtree par la RLS. Sans ce test bloquant, une regression future dans `posture()`
    (ex. un `bypass=True` ajoute par erreur, ou une session non scopee) ne serait
    detectee par aucun test bloquant : A verrait la posture TLS de B, et pourrait
    durcir `enforce` sur la foi de donnees qui ne sont pas les siennes.
    """
    from datetime import date

    from app.db.models import ReportRow
    from app.services.tls_posture import posture

    tid_a, tid_b = seed_two_tenants

    with get_session() as db:  # plan worker : on seme chez B
        rep_b = db.query(Report).filter_by(tenant_id=tid_b).first()
        db.add(ReportRow(tenant_id=tid_b, report_id=rep_b.id, data={
            "kind": "summary", "report_date": date.today().isoformat(),
            "policy_domain": "tenant-b-test.com",
            "successful_sessions": 100, "failed_sessions": 5}))
        # Une ligne `failure` aussi -- pas seulement un `summary`. Sans elle, ce test
        # ne prouvait que la moitie de l'isolation : `sessions_total` (issu des seules
        # lignes `summary`) pouvait bien rester a 0 chez A pendant qu'une regression
        # future laisserait fuiter les lignes `failure` de B dans `p["failures"]`,
        # sans qu'aucun test bloquant ne s'en apercoive.
        db.add(ReportRow(tenant_id=tid_b, report_id=rep_b.id, data={
            "kind": "failure", "report_date": date.today().isoformat(),
            "policy_domain": "tenant-b-test.com",
            "result_type": "certificate-expired",
            "sending_mta_ip": "203.0.113.44",
            "receiving_mx_hostname": "mx.tenant-b-test.com",
            "failure_sessions": 3}))
        # Un rapport TLS de B, entier, jamais lu (`reports_unreadable` -- voir
        # `tls_posture.py`). Il ne laisse AUCUNE ReportRow, donc les deux assertions
        # ci-dessus ne peuvent pas le voir : sans cette troisieme ligne de semis,
        # une regression future qui ferait fuiter `reports_unreadable` de B vers A
        # (ex. un `bypass=True` ajoute par erreur) ne serait detectee par aucun test
        # bloquant.
        rep_b_illisible = Report(tenant_id=tid_b, email_id=rep_b.email_id,
                                 source_type="attachment", status="failed",
                                 profile_id="_default_tlsrpt_json", kind="dmarc")
        db.add(rep_b_illisible)
        db.flush()
        rep_b_illisible_id = str(rep_b_illisible.id)

        # ParsingError chez B, du code que la sous-requete EXISTS de
        # `reports_unreadable` doit justement EXCLURE du compte
        # (`_CODES_ECARTES_A_JUSTE_TITRE`, voir tls_posture.py). Avant ce correctif,
        # aucun test bloquant ne semait de ParsingError chez B : une regression qui
        # romprait la correlation sur `report_id` (ex. un `ParsingError.code.in_(...)`
        # ecrit SANS la jointure sur `Report.id`, qui rendrait la sous-requete VRAIE
        # pour n'importe quel rapport des qu'un SEUL ParsingError du bon code existe
        # QUELQUE PART en base -- y compris chez un autre tenant) ne serait detectee
        # par aucun test bloquant. Ce semis force cette sous-requete a s'executer sur
        # des donnees d'un AUTRE tenant que celui qui lit sa posture, exactement le
        # scenario ou une telle regression se verrait.
        rep_b_usurpe = Report(tenant_id=tid_b, email_id=rep_b.email_id,
                              source_type="attachment", status="failed",
                              profile_id="_default_tlsrpt_json", kind="dmarc")
        db.add(rep_b_usurpe)
        db.flush()
        db.add(ParsingError(tenant_id=tid_b, email_id=rep_b.email_id,
                            report_id=rep_b_usurpe.id, severity="fatal",
                            code="DMARC_DOMAIN_MISMATCH",
                            message="rapport concernant un autre domaine, rejete"))
        rep_b_usurpe_id = str(rep_b_usurpe.id)
        db.commit()

    try:
        with tenant_scoped_session(tenant_id=tid_a) as db:  # A lit sa propre posture
            p = posture(db, days=30)
            assert p["sessions_total"] == 0, "A voit des sessions TLS de B"
            assert p["failures"] == [], "A voit les echecs detailles de B"
            assert p["reports_unreadable"] == 0, (
                "A voit un rapport TLS illisible de B, ou la sous-requete EXISTS "
                "sur ParsingError fuite entre tenants")
    finally:
        with get_session() as db:
            db.query(ParsingError).filter_by(report_id=rep_b_usurpe_id).delete(
                synchronize_session=False)
            db.query(ReportRow).filter(
                ReportRow.data["policy_domain"].astext == "tenant-b-test.com").delete(
                synchronize_session=False)
            db.query(Report).filter(Report.id.in_(
                [rep_b_illisible_id, rep_b_usurpe_id])).delete(
                synchronize_session=False)
            db.commit()


def test_ip_TLS_vue_par_b_est_invisible_de_a(seed_two_tenants):
    """Même principe que pour une IP DMARC : le contrôle d'appartenance de /ip-intel
    interroge maintenant DEUX champs. Il doit rester aveugle aux lignes des autres.
    """
    from app.db.models import ReportRow

    tid_a, tid_b = seed_two_tenants

    with get_session() as db:
        rep_b = db.query(Report).filter_by(tenant_id=tid_b).first()
        db.add(ReportRow(tenant_id=tid_b, report_id=rep_b.id,
                         data={"kind": "failure", "sending_mta_ip": "198.51.100.77",
                               "result_type": "starttls-not-supported",
                               "failure_sessions": 9}))
        db.commit()

    try:
        with tenant_scoped_session(tenant_id=tid_a) as db:
            vues = (db.query(ReportRow)
                      .filter(ReportRow.data["sending_mta_ip"].astext == "198.51.100.77")
                      .all())
            assert vues == [], "A voit une ligne TLS de B"
    finally:
        with get_session() as db:
            db.query(ReportRow).filter(
                ReportRow.data["sending_mta_ip"].astext == "198.51.100.77").delete(
                synchronize_session=False)
            db.commit()


def test_les_alertes_d_un_tenant_sont_invisibles_de_l_autre(seed_two_tenants):
    """Les alertes sont des données de client : `alert` porte un tenant_id, donc la RLS
    s'applique — aucune exception, contrairement au cache ip_intel.
    """
    from datetime import datetime, timezone

    from app.db.models import Alert

    tid_a, tid_b = seed_two_tenants

    with get_session() as db:
        db.add(Alert(tenant_id=tid_b, kind="never_reported", dedup_key="",
                     severity="critical", payload={"domain": "b"},
                     opened_at=datetime.now(timezone.utc)))
        db.commit()

    try:
        with tenant_scoped_session(tenant_id=tid_a) as db:
            assert db.query(Alert).all() == []
    finally:
        with get_session() as db:
            db.query(Alert).filter_by(tenant_id=tid_b).delete()
            db.commit()


def test_le_balayage_scope_bien_chaque_tenant(seed_two_tenants, monkeypatch):
    """Le risque phare de ce chantier, dans le fichier BLOQUANT : les détecteurs
    d'alerte (`app/services/alerting/detectors/`) n'ont AUCUN filtre `tenant_id`
    applicatif -- ils comptent entièrement sur la RLS (CLAUDE.md). `reconcile_tenant`
    (`app/workers/tasks.py`) DOIT donc leur ouvrir une session scopée par tenant
    (`tenant_scoped_session`), jamais la session worker (`get_session()`, BYPASSRLS).

    Copie volontaire, dans le fichier bloquant, de
    `test_le_balayage_scope_bien_chaque_tenant` de `tests/test_alert_sweep.py` --
    redondance assumée sur un test de sécurité (voir le rapport de tâche). Si ce
    correctif venait à être défait (un `reconcile_tenant` rebranché sur `get_session()`),
    ce fichier bloquant doit, lui aussi, virer au rouge.

    `seed_two_tenants` donne à CHAQUE tenant un rapport frais -- on casse volontairement
    cette symétrie pour rendre le test discriminant : A perd son unique rapport et
    devient donc, seul des deux, un domaine qui n'a JAMAIS parlé. Les deux tenants sont
    vieillis au-delà du délai de grâce (`alert_onboarding_grace_days`), sinon le
    détecteur `never_reported` ne se prononce jamais, quel que soit l'état des rapports.

    Sous RLS correcte, `reconcile_tenant(tid_a)` ouvre une session qui ne voit AUCUN
    rapport (ni le sien -- il n'en a plus -- ni celui de B) -> `never_reported` se lève.
    Sous bypass (le bug), la même requête verrait AUSSI le rapport de B ->
    `db.query(Report.id).first()` ne serait plus None -> A ne lèverait RIEN. C'est cette
    différence de COMPORTEMENT -- pas la valeur de `tenant_id` sur l'alerte -- que ce
    test attrape (`db.get(Tenant, tenant_id)` renverrait le bon tenant même sous bypass).

    PREUVE (voir le rapport de tâche) : ce test est ROUGE quand `reconcile_tenant` est
    temporairement branché sur `get_session()` (bypass) au lieu de
    `tenant_scoped_session(...)`, et VERT une fois le code correct remis en place.
    """
    monkeypatch.setattr(tasks.notify_alerts, "delay", lambda *a, **k: None)
    tid_a, tid_b = seed_two_tenants

    vieux = datetime.now(timezone.utc) - timedelta(days=30)
    with get_session() as db:  # plan worker : préparation du scénario, pas le test lui-même
        db.query(Report).filter_by(tenant_id=tid_a).delete(synchronize_session=False)
        db.get(Tenant, tid_a).created_at = vieux
        db.get(Tenant, tid_b).created_at = vieux
        db.commit()

    try:
        tasks.reconcile_tenant(tid_a)
        tasks.reconcile_tenant(tid_b)

        with get_session() as db:  # lecture système -- seulement pour VÉRIFIER le résultat
            alertes_a = db.query(Alert).filter_by(tenant_id=tid_a).all()
            alertes_b = db.query(Alert).filter_by(tenant_id=tid_b).all()

        assert [a.kind for a in alertes_a] == ["never_reported"]
        assert str(alertes_a[0].tenant_id) == tid_a
        assert alertes_a[0].payload["domain"] == "tenant-a-test.com"  # PAS le domaine de B

        assert alertes_b == []   # B a un rapport frais : rien à lever pour lui
    finally:
        with get_session() as db:
            db.query(Alert).filter_by(tenant_id=tid_a).delete(synchronize_session=False)
            db.query(Alert).filter_by(tenant_id=tid_b).delete(synchronize_session=False)
            db.commit()
