"""Procédure de mise en conformité d'un domaine.

Le point réellement dangereux est le `mx:` de la politique MTA-STS : il doit
correspondre au CERTIFICAT présenté par le MX, pas seulement à son nom. S'y tromper,
en mode enforce, fait **perdre du courrier** — les expéditeurs conformes refusent la
livraison. On le déduit donc du MX réel plutôt que de laisser deviner.

Le reste des tests couvre la détection des erreurs réellement commises pendant la mise
en place : un `V=DMARC1` majuscule, un `v=TLSRPTV1` dont la casse est normative, un
enregistrement publié mais pointant vers la mauvaise boîte.
"""
from app.services.onboarding import Step, build, mx_policy_for

MAILBOX = "dmarc.lsi@lsiparis.tech"
REPORTING = "lsiparis.tech"
STS_IP = "203.0.113.10"


def steps_by_key(cl) -> dict[str, Step]:
    return {s.key: s for s in cl.steps}


# ------------------------------------------------- le calcul qui peut coûter du courrier
def test_mx_microsoft_donne_le_joker_du_certificat():
    """Le certificat Microsoft porte *.mail.protection.outlook.com : c'est CE motif
    qu'il faut écrire, pas le nom du MX du client."""
    mx = ["contoso-com.mail.protection.outlook.com"]
    assert mx_policy_for(mx) == ["*.mail.protection.outlook.com"]


def test_mx_auto_heberge_donne_le_nom_exact():
    assert mx_policy_for(["mail.exemple.com"]) == ["mail.exemple.com"]


def test_mx_mixte_ne_generalise_pas():
    """Un seul MX hors Microsoft, et le joker ne couvre plus tout : on liste les noms."""
    mx = ["a.mail.protection.outlook.com", "backup.exemple.com"]
    assert mx_policy_for(mx) == mx


def test_aucun_mx_ne_propose_rien():
    assert mx_policy_for([]) == []


# ------------------------------------------------- la checklist elle-même
def test_la_procedure_couvre_les_six_enregistrements(monkeypatch):
    monkeypatch.setattr("app.services.onboarding._txt", lambda name: [])
    monkeypatch.setattr("app.services.onboarding._has_a", lambda name: [])
    monkeypatch.setattr("app.services.onboarding.resolve_mx", lambda d: [])
    monkeypatch.setattr("app.services.onboarding._served_policy",
                        lambda d: ("todo", "injoignable"))

    cl = build("exemple.com", mailbox=MAILBOX, reporting_domain=REPORTING,
               mta_sts_ip=STS_IP)
    keys = [s.key for s in cl.steps]
    assert keys == ["dmarc", "dmarc_auth", "tlsrpt", "tlsrpt_auth",
                    "mta_sts_host", "mta_sts_policy", "mta_sts_txt"]
    assert all(s.status == "todo" for s in cl.steps)

    # Les autorisations vont dans la zone de collecte, pas celle du client : c'est la
    # confusion la plus fréquente.
    by = steps_by_key(cl)
    assert by["dmarc"].zone == "exemple.com"
    assert by["dmarc_auth"].zone == REPORTING
    assert by["tlsrpt_auth"].zone == REPORTING


def test_tout_est_conforme(monkeypatch):
    txt = {
        "_dmarc.exemple.com": [f"v=DMARC1; p=none; rua=mailto:{MAILBOX}; adkim=s;"],
        "exemple.com._report._dmarc.lsiparis.tech": ["v=DMARC1"],
        "_smtp._tls.exemple.com": [f"v=TLSRPTv1; rua=mailto:{MAILBOX}"],
        "exemple.com._report._smtp._tls.lsiparis.tech": ["v=TLSRPTv1"],
        "_mta-sts.exemple.com": ["v=STSv1; id=20260714a"],
    }
    monkeypatch.setattr("app.services.onboarding._txt", lambda n: txt.get(n, []))
    monkeypatch.setattr("app.services.onboarding._has_a", lambda n: [STS_IP])
    monkeypatch.setattr("app.services.onboarding.resolve_mx",
                        lambda d: ["x-com.mail.protection.outlook.com"])
    monkeypatch.setattr("app.services.onboarding._served_policy",
                        lambda d: ("ok", "servie — mode=testing"))

    cl = build("exemple.com", mailbox=MAILBOX, reporting_domain=REPORTING,
               mta_sts_ip=STS_IP)
    assert all(s.status == "ok" for s in cl.steps)
    assert cl.mx_policy == ["*.mail.protection.outlook.com"]


def test_dmarc_publie_mais_pointant_ailleurs_est_signale(monkeypatch):
    """Le cas vicieux : l'enregistrement existe, tout a l'air fait, et les rapports
    partent chez quelqu'un d'autre (un prestataire, un ancien outil)."""
    monkeypatch.setattr("app.services.onboarding._txt", lambda n: (
        ["v=DMARC1; p=reject; rua=mailto:xyz@rua.easydmarc.eu;"]
        if n == "_dmarc.exemple.com" else []))
    monkeypatch.setattr("app.services.onboarding._has_a", lambda n: [])
    monkeypatch.setattr("app.services.onboarding.resolve_mx", lambda d: [])
    monkeypatch.setattr("app.services.onboarding._served_policy", lambda d: ("todo", ""))

    step = steps_by_key(build("exemple.com", mailbox=MAILBOX,
                              reporting_domain=REPORTING, mta_sts_ip=STS_IP))["dmarc"]
    assert step.status == "warn"
    assert "ne vont PAS vers cette plateforme" in step.detail


def test_la_casse_de_tlsrpt_est_normative(monkeypatch):
    """RFC 8460 §3 définit la version en octets hexadécimaux : 'v=TLSRPTV1' est
    invalide. Erreur réellement commise en production."""
    monkeypatch.setattr("app.services.onboarding._txt", lambda n: (
        ["v=TLSRPTV1"] if n.endswith("._report._smtp._tls.lsiparis.tech") else []))
    monkeypatch.setattr("app.services.onboarding._has_a", lambda n: [])
    monkeypatch.setattr("app.services.onboarding.resolve_mx", lambda d: [])
    monkeypatch.setattr("app.services.onboarding._served_policy", lambda d: ("todo", ""))

    step = steps_by_key(build("exemple.com", mailbox=MAILBOX,
                              reporting_domain=REPORTING,
                              mta_sts_ip=STS_IP))["tlsrpt_auth"]
    assert step.status == "warn"
    assert "casse est normative" in step.detail


def test_hote_mta_sts_pointant_ailleurs_est_signale(monkeypatch):
    monkeypatch.setattr("app.services.onboarding._txt", lambda n: [])
    monkeypatch.setattr("app.services.onboarding._has_a", lambda n: ["198.51.100.7"])
    monkeypatch.setattr("app.services.onboarding.resolve_mx", lambda d: [])
    monkeypatch.setattr("app.services.onboarding._served_policy", lambda d: ("todo", ""))

    step = steps_by_key(build("exemple.com", mailbox=MAILBOX,
                              reporting_domain=REPORTING,
                              mta_sts_ip=STS_IP))["mta_sts_host"]
    assert step.status == "warn"
    assert STS_IP in step.detail and "198.51.100.7" in step.detail
