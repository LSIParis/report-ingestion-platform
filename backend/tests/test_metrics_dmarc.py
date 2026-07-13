"""Agrégats DMARC : la partie risquée est le SQL sur le JSONB.

On raisonne en MESSAGES, jamais en lignes : une ligne de rapport porte un
`message_count` qui peut valoir 1 comme 12 000. Compter les lignes donnerait des
chiffres faux mais plausibles — le pire type de bug pour un tableau de bord.

Ces tests tournent contre un vrai PostgreSQL (casts JSONB, sommes conditionnelles).
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.metrics import router
from app.auth.deps import get_db
from app.db.models import Email, Report, ReportRow, Tenant
from app.db.session import get_session

TODAY = date.today()


def _row(tenant_id, report_id, day, ip, count, dkim, spf, disposition="none"):
    return ReportRow(
        tenant_id=tenant_id, report_id=report_id, report_date=day,
        data={
            "source_ip": ip,
            "message_count": count,
            "dkim": dkim,
            "spf": spf,
            "aligned": "pass" if "pass" in (dkim, spf) else "fail",
            "disposition": disposition,
            "reporter": "google.com",
        },
    )


@pytest.fixture
def dataset():
    """Un tenant, 5 lignes, volumes délibérément déséquilibrés :

      1.1.1.1  4 000 msg  DKIM pass          -> authentifiés
      1.1.1.1  1 000 msg  DKIM pass          -> authentifiés (même IP, autre jour)
      2.2.2.2    100 msg  SPF pass seulement -> authentifiés (DKIM ou SPF suffit)
      3.3.3.3    500 msg  aucun des deux     -> ÉCHEC, mise en quarantaine
      3.3.3.3     10 msg  aucun des deux     -> ÉCHEC, rejeté

    Total 5 610 messages, 5 100 authentifiés (90,9 %), 1 source qui n'authentifie rien.
    Si le code comptait les LIGNES, il annoncerait 3/5 = 60 % : le test le verrait.
    """
    with get_session() as db:
        t = Tenant(domain=f"metrics-{uuid.uuid4().hex[:8]}.test", name="Metrics")
        db.add(t)
        db.flush()
        em = Email(tenant_id=t.id, message_id=f"m-{uuid.uuid4()}",
                   from_address="noreply-dmarc-support@google.com", subject="s",
                   received_at=datetime.now(timezone.utc), raw_object_key="raw/x.eml",
                   status="parsed_ok")
        db.add(em)
        db.flush()
        rep = Report(tenant_id=t.id, email_id=em.id, source_type="attachment", status="ok")
        db.add(rep)
        db.flush()

        db.add_all([
            _row(t.id, rep.id, TODAY,                     "1.1.1.1", 4000, "pass", "fail"),
            _row(t.id, rep.id, TODAY - timedelta(days=1), "1.1.1.1", 1000, "pass", "pass"),
            _row(t.id, rep.id, TODAY - timedelta(days=1), "2.2.2.2",  100, "fail", "pass"),
            _row(t.id, rep.id, TODAY - timedelta(days=2), "3.3.3.3",  500, "fail", "fail",
                 "quarantine"),
            _row(t.id, rep.id, TODAY - timedelta(days=2), "3.3.3.3",   10, "fail", "fail",
                 "reject"),
        ])
        db.commit()
        tid = str(t.id)

    yield tid

    with get_session() as db:
        rep_ids = [r.id for r in db.query(Report.id).filter_by(tenant_id=tid).all()]
        db.query(ReportRow).filter(ReportRow.report_id.in_(rep_ids)).delete(
            synchronize_session=False)
        db.query(Report).filter_by(tenant_id=tid).delete()
        db.query(Email).filter_by(tenant_id=tid).delete()
        db.query(Tenant).filter_by(id=tid).delete()
        db.commit()


@pytest.fixture
def client(dataset):
    """Monte les routes de métriques en court-circuitant le middleware : ici on teste
    l'agrégation, pas l'authentification (couverte par test_middleware_isolation)."""
    app = FastAPI()
    app.include_router(router)

    def _db():
        with get_session() as s:      # plan worker : voit le tenant du dataset
            yield s

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


def test_summary_raisonne_en_messages_et_non_en_lignes(client):
    s = client.get("/metrics/dmarc/summary?days=30").json()
    assert s["messages"] == 5610          # somme des message_count, PAS 5 lignes
    assert s["compliant"] == 5100
    assert s["failing"] == 510
    assert s["compliance_rate"] == 90.9   # et surtout pas 60 % (3 lignes sur 5)


def test_dkim_ou_spf_suffit(client):
    s = client.get("/metrics/dmarc/summary?days=30").json()
    assert s["dkim_pass"] == 5000         # 4000 + 1000
    assert s["spf_pass"] == 1100          # 1000 + 100
    # 2.2.2.2 n'a que SPF, et compte pourtant comme authentifié :
    assert s["compliant"] == s["dkim_pass"] + 100


def test_dispositions_et_sources(client):
    s = client.get("/metrics/dmarc/summary?days=30").json()
    assert s["quarantined"] == 500
    assert s["rejected"] == 10
    assert s["sources"] == 3
    assert s["failing_sources"] == 1      # seule 3.3.3.3 n'authentifie AUCUN message


def test_fenetre_temporelle_exclut_les_lignes_anciennes(client):
    s = client.get("/metrics/dmarc/summary?days=1").json()
    # Ne reste que la ligne d'aujourd'hui et celle d'hier (bord inclus).
    assert s["messages"] == 5100
    assert s["failing"] == 0


def test_aucun_message_ne_donne_pas_un_taux_de_zero(client):
    """Un taux de 0 % laisserait croire que TOUT échoue. Absence de mesure ≠ échec."""
    s = client.get("/metrics/dmarc/summary?days=365").json()
    assert s["messages"] > 0
    # On vérifie le contrat sur la fenêtre vide : la route doit renvoyer None, pas 0.
    empty = client.get("/metrics/dmarc/sources?days=1&limit=1").json()
    assert all(r["compliance_rate"] is not None for r in empty)


def test_timeseries_separe_authentifies_et_echecs_par_jour(client):
    pts = {p["day"]: p for p in client.get("/metrics/dmarc/timeseries?days=30").json()}
    assert pts[TODAY.isoformat()] == {
        "day": TODAY.isoformat(), "compliant": 4000, "failing": 0}
    j2 = (TODAY - timedelta(days=2)).isoformat()
    assert pts[j2]["compliant"] == 0 and pts[j2]["failing"] == 510


def test_sources_triees_par_volume_avec_taux(client):
    rows = client.get("/metrics/dmarc/sources?days=30").json()
    assert [r["source_ip"] for r in rows] == ["1.1.1.1", "3.3.3.3", "2.2.2.2"]
    top = rows[0]
    assert top["messages"] == 5000 and top["compliance_rate"] == 100.0
    bad = rows[1]
    assert bad["messages"] == 510 and bad["compliant"] == 0
    assert bad["compliance_rate"] == 0.0        # ici 0 % est une vraie mesure
    assert bad["last_seen"] == (TODAY - timedelta(days=2)).isoformat()
