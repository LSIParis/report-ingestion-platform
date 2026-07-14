"""Politique MTA-STS servie par l'API, à partir de la base.

C'est le seul réglage de la plateforme qui peut faire **perdre du courrier** : en mode
`enforce`, un expéditeur qui ne trouve pas le MX du domaine dans la politique **refuse de
livrer**. Rien ne casse de notre côté — ce sont les expéditeurs qui renoncent, chacun
silencieusement. Aucune alerte, aucun log : juste du courrier qui n'arrive plus.

D'où les gardes testés ici en priorité.
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import router as admin_router
from app.api.mta_sts import policy_id, render
from app.api.mta_sts import router as sts_router
from app.auth.middleware import TenantContext
from app.db.models import Tenant, TenantMatchingRule
from app.db.session import get_session


@pytest.fixture
def client():
    app = FastAPI()
    ctx = TenantContext(user="admin@lsi.test", role="platform_admin", tenant_ids=(),
                        active_tenant=None, bypass=True)

    @app.middleware("http")
    async def inject_ctx(request, call_next):
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(admin_router)
    app.include_router(sts_router)
    return TestClient(app)


@pytest.fixture
def domain(client):
    """Un domaine dont le MX est Microsoft 365 (résolu en vrai à la création)."""
    d = f"sts-{uuid.uuid4().hex[:8]}.test"
    tid = client.post("/admin/tenants", json={"domain": d}).json()["id"]

    yield tid, d

    with get_session() as db:
        db.query(TenantMatchingRule).filter_by(tenant_id=tid).delete()
        db.query(Tenant).filter_by(id=tid).delete()
        db.commit()


def _set_mx(tid, mx, mode="testing"):
    with get_session() as db:
        t = db.get(Tenant, tid)
        t.mta_sts_mx, t.mta_sts_mode = mx, mode
        t.mta_sts_updated_at = datetime.now(timezone.utc)
        db.commit()


# --------------------------------------------------- rendu de la politique
def test_le_rendu_respecte_la_rfc():
    t = Tenant(domain="x.test", name="X", mta_sts_mode="testing",
               mta_sts_max_age=86400, mta_sts_mx=["*.mail.protection.outlook.com"])
    body = render(t)
    # RFC 8461 §3.2 : lignes séparées par CRLF.
    assert body == ("version: STSv1\r\nmode: testing\r\n"
                    "mx: *.mail.protection.outlook.com\r\nmax_age: 86400\r\n")


def test_plusieurs_mx_donnent_plusieurs_lignes():
    t = Tenant(domain="x.test", name="X", mta_sts_mode="enforce",
               mta_sts_max_age=604800, mta_sts_mx=["mx1.x.test", "mx2.x.test"])
    assert "mx: mx1.x.test\r\nmx: mx2.x.test\r\n" in render(t)


def test_l_id_derive_du_contenu_de_la_politique():
    """L'id doit changer dès que la politique change — sinon les expéditeurs gardent
    l'ancienne en cache jusqu'à expiration de max_age, et on ne peut rien y faire.

    Il est dérivé du CONTENU, pas d'un horodatage : un horodatage à la seconde donnait le
    même id à deux modifications rapprochées (bug réel, attrapé par le test suivant)."""
    a = Tenant(domain="x.test", name="X", mta_sts_mode="testing",
               mta_sts_max_age=86400, mta_sts_mx=["mx.x.test"])
    b = Tenant(domain="x.test", name="X", mta_sts_mode="enforce",   # mode différent
               mta_sts_max_age=86400, mta_sts_mx=["mx.x.test"])
    c = Tenant(domain="x.test", name="X", mta_sts_mode="testing",
               mta_sts_max_age=604800, mta_sts_mx=["mx.x.test"])    # max_age différent

    assert policy_id(a) != policy_id(b)
    assert policy_id(a) != policy_id(c)


def test_une_politique_identique_garde_le_meme_id():
    """Réenregistrer sans rien changer ne doit PAS obliger à retoucher le DNS."""
    kw = dict(domain="x.test", name="X", mta_sts_mode="testing",
              mta_sts_max_age=86400, mta_sts_mx=["mx.x.test"])
    assert policy_id(Tenant(**kw)) == policy_id(Tenant(**kw))


# --------------------------------------------------- service HTTP
def test_la_politique_est_servie_sur_le_bon_hote(client, domain):
    tid, d = domain
    _set_mx(tid, ["mail.x.test"])

    r = client.get("/.well-known/mta-sts.txt", headers={"Host": f"mta-sts.{d}"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "mx: mail.x.test" in r.text


def test_un_domaine_inconnu_ne_recoit_pas_la_politique_d_un_autre(client, domain):
    tid, _ = domain
    _set_mx(tid, ["mail.x.test"])
    r = client.get("/.well-known/mta-sts.txt",
                   headers={"Host": "mta-sts.inconnu-total.test"})
    assert r.status_code == 404


def test_aucune_politique_servie_sans_mx(client, domain):
    """Un `mx:` vide serait PIRE que pas de politique : en enforce, aucun serveur ne
    correspondrait et tout le courrier entrant serait refusé. On sert 404."""
    tid, d = domain
    _set_mx(tid, [], mode="testing")
    assert client.get("/.well-known/mta-sts.txt",
                      headers={"Host": f"mta-sts.{d}"}).status_code == 404


def test_mode_none_ne_sert_rien(client, domain):
    tid, d = domain
    _set_mx(tid, ["mail.x.test"], mode="none")
    assert client.get("/.well-known/mta-sts.txt",
                      headers={"Host": f"mta-sts.{d}"}).status_code == 404


# --------------------------------------------------- les gardes qui évitent la panne
def test_enforce_sans_mx_est_refuse(client, domain):
    tid, _ = domain
    r = client.put(f"/admin/tenants/{tid}/mta-sts",
                   json={"mode": "enforce", "max_age": 604800, "mx": []})
    assert r.status_code == 422
    assert "refuserait TOUT le courrier" in r.json()["detail"]


def test_enforce_avec_un_mx_qui_ne_correspond_pas_au_dns_est_refuse(client, domain):
    """L'erreur qui coupe la réception, et elle est silencieuse : rien ne casse chez
    nous, ce sont les expéditeurs qui cessent de livrer."""
    tid, _ = domain          # domaine .test : aucun MX réel
    with get_session() as db:
        db.get(Tenant, tid).mta_sts_mx = ["ancien-mx.example"]
        db.commit()

    # On simule un MX réel différent de celui déclaré.
    import app.api.admin as admin_mod
    real = ["nouveau-mx.example"]
    orig = admin_mod.onboarding.resolve_mx
    admin_mod.onboarding.resolve_mx = lambda d: real
    try:
        r = client.put(f"/admin/tenants/{tid}/mta-sts",
                       json={"mode": "enforce", "max_age": 604800,
                             "mx": ["ancien-mx.example"]})
    finally:
        admin_mod.onboarding.resolve_mx = orig

    assert r.status_code == 409
    assert "ne correspond pas au MX réel" in r.json()["detail"]


def test_testing_reste_permissif(client, domain):
    """En testing, les expéditeurs SIGNALENT sans bloquer : on n'impose donc pas que le
    mx corresponde — c'est justement la phase où l'on découvre qu'il ne correspond pas."""
    tid, _ = domain
    r = client.put(f"/admin/tenants/{tid}/mta-sts",
                   json={"mode": "testing", "max_age": 86400, "mx": ["mx.example"]})
    assert r.status_code == 200


def test_modifier_la_politique_change_son_id(client, domain):
    tid, _ = domain
    a = client.put(f"/admin/tenants/{tid}/mta-sts",
                   json={"mode": "testing", "max_age": 86400,
                         "mx": ["mx.example"]}).json()["policy_id"]
    b = client.put(f"/admin/tenants/{tid}/mta-sts",
                   json={"mode": "testing", "max_age": 604800,
                         "mx": ["mx.example"]}).json()["policy_id"]
    assert a != b     # sinon les expéditeurs garderaient l'ancienne en cache
