"""Le balayage et la notification.

Le test qui compte est `test_le_balayage_scope_bien_chaque_tenant` : les détecteurs
n'ont aucun filtre tenant_id applicatif, ils comptent sur la RLS. Les faire tourner sur la
session du worker (qui BYPASSE la RLS) leur ferait voir TOUS les tenants, et ouvrirait les
alertes d'un client sur le domaine d'un autre. Avec un seul tenant en développement, ce
bug est INVISIBLE.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import Alert, Email, Report, ReportRow, Tenant
from app.db.session import get_session
from app.workers import tasks


@pytest.fixture
def deux_domaines():
    """A et B, tous deux vieux et sans aucun rapport → chacun doit lever never_reported,
    et UNIQUEMENT le sien."""
    vieux = datetime.now(timezone.utc) - timedelta(days=30)
    with get_session() as db:
        a = Tenant(domain=f"sweep-a-{uuid.uuid4().hex[:6]}.test", name="A", created_at=vieux)
        b = Tenant(domain=f"sweep-b-{uuid.uuid4().hex[:6]}.test", name="B", created_at=vieux)
        db.add_all([a, b])
        db.commit()
        ids = (str(a.id), str(b.id))

    yield ids

    with get_session() as db:
        for tid in ids:
            db.query(Alert).filter_by(tenant_id=tid).delete()
            db.query(Tenant).filter_by(id=tid).delete()
        db.commit()


def _alertes(tid):
    with get_session() as db:
        return db.query(Alert).filter_by(tenant_id=tid).all()


def _rapport_recent(tid: str) -> None:
    """Un rapport reçu maintenant pour ce tenant. Distinct de `deux_domaines` (où
    personne n'a de rapport) : sert à rendre `test_le_balayage_scope_bien_chaque_tenant`
    réellement discriminant -- voir son docstring."""
    with get_session() as db:
        em = Email(tenant_id=tid, message_id=f"sweep-{uuid.uuid4()}",
                  from_address="noreply@google.com", subject="rapport",
                  received_at=datetime.now(timezone.utc), raw_object_key="raw/x.eml",
                  status="parsed_ok")
        db.add(em)
        db.flush()
        rep = Report(tenant_id=tid, email_id=em.id, source_type="attachment", status="ok")
        db.add(rep)
        db.commit()


@pytest.fixture
def domaine_b_a_deja_un_rapport(deux_domaines):
    """Comme `deux_domaines` (A et B, vieux, sans rapport), mais B reçoit en plus un
    rapport RÉCENT -- A, lui, reste sans AUCUN rapport. C'est cette asymétrie qui rend le
    test suivant discriminant (voir son docstring)."""
    tid_a, tid_b = deux_domaines
    _rapport_recent(tid_b)

    yield tid_a, tid_b

    # Nettoyage AVANT celui de `deux_domaines` (qui supprime les Tenant) : Report/Email
    # portent une FK vers tenant, il faut les faire disparaître d'abord.
    with get_session() as db:
        reps = [r.id for r in db.query(Report.id).filter_by(tenant_id=tid_b).all()]
        if reps:
            db.query(ReportRow).filter(ReportRow.report_id.in_(reps)).delete(
                synchronize_session=False)
            db.query(Report).filter(Report.id.in_(reps)).delete(synchronize_session=False)
        db.query(Email).filter_by(tenant_id=tid_b).delete(synchronize_session=False)
        db.commit()


def test_le_balayage_ouvre_une_alerte_par_domaine(deux_domaines, monkeypatch):
    monkeypatch.setattr(tasks.notify_alert, "delay", lambda *a, **k: None)

    tasks.sweep_alerts()

    for tid in deux_domaines:
        kinds = [a.kind for a in _alertes(tid)]
        assert kinds == ["never_reported"]


def test_le_balayage_scope_bien_chaque_tenant(domaine_b_a_deja_un_rapport, monkeypatch):
    """Chaque alerte porte le tenant_id du domaine qu'elle concerne — et un seul.

    AVANT ce correctif, ce test N'ÉTAIT PAS discriminant : `alert.tenant_id` et
    `payload["domain"]` viennent tous deux de l'objet `tenant` passé en ARGUMENT au
    détecteur (jamais filtré par la session elle-même) -- pas de ce que la session
    laisse effectivement VOIR. `db.get(Tenant, tenant_id)` par clé primaire renvoie le
    bon tenant même sans RLS : une session bypass (le bug) aurait donc fait passer ces
    deux assertions tout autant.

    Le fixture donne à B un rapport récent et laisse A sans aucun rapport : sous RLS
    correcte, la requête `Report` du détecteur `never_reported` exécutée pour A ne voit
    QUE les rapports de A (aucun) -> A lève l'alerte. Sous bypass (le bug), la même
    requête verrait AUSSI le rapport de B -> `db.query(Report.id).first()` ne serait
    plus None -> A ne lèverait RIEN. C'est cette différence de comportement -- pas la
    valeur de `tenant_id` -- que ce test doit attraper. Preuve dans le rapport de tâche :
    ce test ROUGE avec `reconcile_tenant` branché sur `get_session()` (bypass), VERT
    remis sur `tenant_scoped_session(...)`.
    """
    monkeypatch.setattr(tasks.notify_alert, "delay", lambda *a, **k: None)
    tid_a, tid_b = domaine_b_a_deja_un_rapport

    tasks.sweep_alerts()

    a = _alertes(tid_a)
    assert len(a) == 1
    assert a[0].kind == "never_reported"
    assert str(a[0].tenant_id) == tid_a
    assert a[0].payload["domain"].startswith("sweep-a-")   # PAS le domaine de B

    # B a déjà un rapport récent : rien à lever pour lui -- ni never_reported (il a
    # parlé), ni domain_silent (le rapport est frais).
    assert _alertes(tid_b) == []


def test_le_balayage_ignore_un_domaine_suspendu(deux_domaines, monkeypatch):
    monkeypatch.setattr(tasks.notify_alert, "delay", lambda *a, **k: None)
    tid_a, tid_b = deux_domaines
    with get_session() as db:
        db.get(Tenant, tid_b).status = "suspended"
        db.commit()

    tasks.sweep_alerts()

    assert _alertes(tid_b) == []          # un domaine coupé n'a pas à alerter
    assert len(_alertes(tid_a)) == 1


def test_le_balayage_est_idempotent(deux_domaines, monkeypatch):
    monkeypatch.setattr(tasks.notify_alert, "delay", lambda *a, **k: None)

    tasks.sweep_alerts()
    tasks.sweep_alerts()
    tasks.sweep_alerts()

    for tid in deux_domaines:
        assert len(_alertes(tid)) == 1     # pas de doublon, pas de renotification


def test_un_webhook_en_panne_ne_casse_pas_le_balayage(deux_domaines, monkeypatch):
    """La base est la source de vérité. Un canal en panne fait perdre une NOTIFICATION,
    jamais une ALERTE."""
    def _explose(*a, **k):
        raise RuntimeError("redis indisponible")

    monkeypatch.setattr(tasks.notify_alert, "delay", _explose)

    tasks.sweep_alerts()                   # ne doit pas lever

    for tid in deux_domaines:
        assert len(_alertes(tid)) == 1     # l'alerte est bien en base


# ---------------------------------------------------------------- ordre de notification
def _rapport_tls(tid: str, *, il_y_a_jours: int = 1) -> None:
    """Un rapport TLS en échec, récent -- de quoi faire lever `tls_failure`."""
    quand = datetime.now(timezone.utc) - timedelta(days=il_y_a_jours)
    with get_session() as db:
        domain = db.get(Tenant, tid).domain
        em = Email(tenant_id=tid, message_id=f"sweep-tls-{uuid.uuid4()}",
                  from_address="noreply@google.com", subject="tls",
                  received_at=quand, raw_object_key="raw/x.eml", status="parsed_ok")
        db.add(em)
        db.flush()
        rep = Report(tenant_id=tid, email_id=em.id, source_type="attachment",
                    profile_id="_default_tlsrpt_json", status="ok", created_at=quand)
        db.add(rep)
        db.flush()
        report_date = quand.date().isoformat()
        db.add(ReportRow(tenant_id=tid, report_id=rep.id, data={
            "kind": "summary", "successful_sessions": 100, "failed_sessions": 3,
            "policy_domain": domain, "reporter": "Google Inc.", "report_date": report_date}))
        db.add(ReportRow(tenant_id=tid, report_id=rep.id, data={
            "kind": "failure", "result_type": "certificate-host-mismatch",
            "sending_mta_ip": "203.0.113.5", "receiving_mx_hostname": f"mx.{domain}",
            "failure_sessions": 3, "policy_domain": domain,
            "reporter": "Google Inc.", "report_date": report_date}))
        db.commit()


@pytest.fixture
def domaine_tls():
    """Un tenant tout neuf (pas de délai de grâce à passer), en mode `testing`, prêt à
    recevoir un rapport TLS."""
    with get_session() as db:
        t = Tenant(domain=f"sweep-tls-{uuid.uuid4().hex[:6]}.test", name="TLS",
                   mta_sts_mode="testing")
        db.add(t)
        db.commit()
        tid = str(t.id)

    yield tid

    with get_session() as db:
        reps = [r.id for r in db.query(Report.id).filter_by(tenant_id=tid).all()]
        if reps:
            db.query(ReportRow).filter(ReportRow.report_id.in_(reps)).delete(
                synchronize_session=False)
            db.query(Report).filter(Report.id.in_(reps)).delete(synchronize_session=False)
        db.query(Email).filter_by(tenant_id=tid).delete(synchronize_session=False)
        db.query(Alert).filter_by(tenant_id=tid).delete(synchronize_session=False)
        db.query(Tenant).filter_by(id=tid).delete(synchronize_session=False)
        db.commit()


def test_une_aggravation_de_severite_notifie_la_fermeture_avant_l_ouverture(
        domaine_tls, monkeypatch):
    """Un changement de sévérité (voir `reconciler.py`) ferme l'ancienne alerte ET en
    ouvre une neuve DANS LE MÊME CYCLE -- même kind, même dedup_key, juste une sévérité
    différente. Si on notifiait l'ouverture avant la fermeture, un consommateur naïf du
    webhook (n8n, un script) qui suit une alerte par (kind, dedup_key) verrait "ouverte
    (critical)" PUIS "fermée (warning)", et conclurait à tort que le problème est résolu
    -- alors qu'il vient de s'aggraver et que du courrier est refusé. La fermeture doit
    partir EN PREMIER."""
    evenements = []
    monkeypatch.setattr(tasks.notify_alert, "delay",
                        lambda event, alert_id: evenements.append(event))

    _rapport_tls(domaine_tls)
    tasks.reconcile_tenant(domaine_tls)          # ouvre tls_failure en warning (testing)
    assert evenements == ["opened"]
    evenements.clear()

    with get_session() as db:
        db.get(Tenant, domaine_tls).mta_sts_mode = "enforce"
        db.commit()

    tasks.reconcile_tenant(domaine_tls)          # aggravation : ferme le warning, ouvre le critical

    assert evenements == ["closed", "opened"]


# ---------------------------------------------------------------------- notify_alert
@pytest.fixture
def un_tenant():
    with get_session() as db:
        t = Tenant(domain=f"notif-{uuid.uuid4().hex[:6]}.test", name="Notif")
        db.add(t)
        db.commit()
        tid = str(t.id)

    yield tid

    with get_session() as db:
        db.query(Alert).filter_by(tenant_id=tid).delete()
        db.query(Tenant).filter_by(id=tid).delete()
        db.commit()


def _cree_alerte(tid: str, **kwargs) -> str:
    with get_session() as db:
        a = Alert(tenant_id=tid, kind="never_reported", dedup_key="", severity="critical",
                  payload={"domain": "x.test"}, **kwargs)
        db.add(a)
        db.commit()
        return str(a.id)


def test_une_ouverture_deja_notifiee_ne_repart_pas(un_tenant, monkeypatch):
    """`task_acks_late=True` (app/celery_app.py) = livraison Celery AT-LEAST-ONCE : un
    worker tué après l'envoi mais avant l'acquittement REJOUE la tâche. Sans garde, la
    même ouverture repartirait une deuxième fois sur le webhook."""
    appels = []
    monkeypatch.setattr(tasks.webhook, "envoyer",
                        lambda event, alerte, tenant: appels.append(event) or True)

    aid = _cree_alerte(un_tenant, opened_notified_at=datetime.now(timezone.utc))

    tasks.notify_alert("opened", aid)

    assert appels == []


def test_une_fermeture_part_meme_si_l_ouverture_a_deja_ete_notifiee(un_tenant, monkeypatch):
    """Une alerte est notifiée deux fois LÉGITIMEMENT dans sa vie -- à l'ouverture, puis
    à la fermeture. La garde contre le rejeu ne doit pas confondre les deux : c'est tout
    l'intérêt d'avoir deux colonnes (`opened_notified_at` / `closed_notified_at`)."""
    appels = []
    monkeypatch.setattr(tasks.webhook, "envoyer",
                        lambda event, alerte, tenant: appels.append(event) or True)

    now = datetime.now(timezone.utc)
    aid = _cree_alerte(un_tenant, opened_notified_at=now, closed_at=now)

    tasks.notify_alert("closed", aid)

    assert appels == ["closed"]
    with get_session() as db:
        a = db.get(Alert, aid)
        assert a.closed_notified_at is not None


def test_une_fermeture_deja_notifiee_ne_repart_pas(un_tenant, monkeypatch):
    """Symétrique : la garde protège aussi la fermeture elle-même contre le rejeu."""
    appels = []
    monkeypatch.setattr(tasks.webhook, "envoyer",
                        lambda event, alerte, tenant: appels.append(event) or True)

    now = datetime.now(timezone.utc)
    aid = _cree_alerte(un_tenant, opened_notified_at=now, closed_at=now,
                       closed_notified_at=now)

    tasks.notify_alert("closed", aid)

    assert appels == []


def test_notify_alert_tenant_supprime_ne_casse_pas(monkeypatch):
    """Le tenant peut disparaître entre l'ouverture d'une alerte et l'envoi (différé, ou
    rejoué) de sa notification. Garde symétrique à celle qui existe déjà pour l'alerte
    introuvable : on abandonne silencieusement, on ne casse jamais la tâche Celery pour
    ça. (Les contraintes FK de la table `alert` empêchent de reproduire cet état avec de
    vraies lignes en base -- on double la session pour isoler la garde elle-même.)"""
    class _Alerte:
        id = uuid.uuid4()
        tenant_id = uuid.uuid4()

    class _FakeDB:
        def get(self, model, pk):
            return _Alerte() if model is Alert else None   # le tenant a disparu

        def commit(self):
            pass

    class _FakeSessionCtx:
        def __enter__(self):
            return _FakeDB()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(tasks, "get_session", lambda: _FakeSessionCtx())
    appels = []
    monkeypatch.setattr(tasks.webhook, "envoyer", lambda *a, **k: appels.append(a) or True)

    tasks.notify_alert("opened", "peu-importe")     # ne doit pas lever

    assert appels == []
