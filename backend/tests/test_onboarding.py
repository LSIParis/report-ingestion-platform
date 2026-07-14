"""Procédure de mise en conformité d'un domaine.

Le point réellement dangereux est le `mx:` de la politique MTA-STS : il doit
correspondre au CERTIFICAT présenté par le MX, pas seulement à son nom. S'y tromper, en
mode enforce, fait **perdre du courrier** — les expéditeurs conformes refusent la
livraison. On le déduit donc du MX réel plutôt que de laisser deviner.

Le reste couvre les erreurs réellement commises, y compris par cet outil lui-même :
 - un enregistrement publié mais dont le rua pointe ailleurs ;
 - un `v=TLSRPTV1` majuscule (casse normative en RFC 8460, contrairement à DMARC) ;
 - la confusion entre la boîte des rapports DMARC et celle des rapports TLS ;
 - l'exigence d'une autorisation croisée pour un domaine qui héberge lui-même la boîte.
"""
from app.services.onboarding import Step, build, mx_policy_for

MAILBOX = "dmarc.lsi@lsiparis.tech"
TLSRPT_MAILBOX = "tls_rpt.lsi@lsiparis.tech"
REPORTING = "lsiparis.tech"
STS_IP = "203.0.113.10"


def steps_by_key(cl) -> dict[str, Step]:
    return {s.key: s for s in cl.steps}


def run(domain="exemple.com"):
    return build(domain, mailbox=MAILBOX, tlsrpt_mailbox=TLSRPT_MAILBOX,
                 reporting_domain=REPORTING, mta_sts_ip=STS_IP)


def stub(monkeypatch, *, txt=None, a=None, mx=None, policy=("todo", "")):
    txt = txt or {}
    monkeypatch.setattr("app.services.onboarding._txt", lambda n: txt.get(n, []))
    monkeypatch.setattr("app.services.onboarding._has_a", lambda n: a or [])
    monkeypatch.setattr("app.services.onboarding.resolve_mx", lambda d: mx or [])
    monkeypatch.setattr("app.services.onboarding._served_policy", lambda d: policy)


# ---------------------------------------- le calcul qui peut coûter du courrier
def test_mx_microsoft_donne_le_joker_du_certificat():
    """Le certificat Microsoft porte *.mail.protection.outlook.com : c'est CE motif
    qu'il faut écrire, pas le nom du MX du client."""
    assert mx_policy_for(["contoso-com.mail.protection.outlook.com"]) == [
        "*.mail.protection.outlook.com"]


def test_mx_auto_heberge_donne_le_nom_exact():
    assert mx_policy_for(["mail.exemple.com"]) == ["mail.exemple.com"]


def test_mx_mixte_ne_generalise_pas():
    """Un seul MX hors Microsoft, et le joker ne couvre plus tout : on liste les noms."""
    mx = ["a.mail.protection.outlook.com", "backup.exemple.com"]
    assert mx_policy_for(mx) == mx


def test_aucun_mx_ne_propose_rien():
    assert mx_policy_for([]) == []


# ---------------------------------------- la checklist
def test_la_procedure_couvre_les_sept_etapes(monkeypatch):
    stub(monkeypatch)
    cl = run()
    assert [s.key for s in cl.steps] == [
        "dmarc", "dmarc_auth", "tlsrpt", "tlsrpt_auth",
        "mta_sts_host", "mta_sts_policy", "mta_sts_txt"]
    assert all(s.status == "todo" for s in cl.steps)

    # Les autorisations vont dans la zone de COLLECTE, pas celle du client : c'est la
    # confusion la plus fréquente.
    by = steps_by_key(cl)
    assert by["dmarc"].zone == "exemple.com"
    assert by["dmarc_auth"].zone == REPORTING
    assert by["tlsrpt_auth"].zone == REPORTING


def test_tout_est_conforme(monkeypatch):
    stub(monkeypatch,
         txt={
             "_dmarc.exemple.com": [f"v=DMARC1; p=none; rua=mailto:{MAILBOX}; adkim=s;"],
             "exemple.com._report._dmarc.lsiparis.tech": ["v=DMARC1"],
             "_smtp._tls.exemple.com": [f"v=TLSRPTv1; rua=mailto:{TLSRPT_MAILBOX}"],
             "exemple.com._report._smtp._tls.lsiparis.tech": ["v=TLSRPTv1"],
             "_mta-sts.exemple.com": ["v=STSv1; id=20260714a"],
         },
         a=[STS_IP], mx=["x-com.mail.protection.outlook.com"],
         policy=("ok", "servie — mode=testing"))

    cl = run()
    assert all(s.status == "ok" for s in cl.steps)
    assert cl.mx_policy == ["*.mail.protection.outlook.com"]


# ---------------------------------------- les pièges
def test_dmarc_publie_mais_pointant_ailleurs_est_signale(monkeypatch):
    """Le cas vicieux : l'enregistrement existe, tout a l'air fait, et les rapports
    partent chez un tiers (un prestataire, un ancien outil)."""
    stub(monkeypatch, txt={
        "_dmarc.exemple.com": ["v=DMARC1; p=reject; rua=mailto:xyz@rua.easydmarc.eu;"]})
    step = steps_by_key(run())["dmarc"]
    assert step.status == "warn"
    assert "ne vont PAS vers cette plateforme" in step.detail


def test_tlsrpt_pointant_sur_la_boite_dmarc_est_signale(monkeypatch):
    """Les rapports TLS vont dans une boîte DISTINCTE de celle des rapports DMARC.
    Confondre les deux publie un enregistrement d'apparence correcte qui envoie les
    rapports au mauvais endroit — erreur commise par cet outil lui-même."""
    stub(monkeypatch, txt={
        "_smtp._tls.exemple.com": [f"v=TLSRPTv1; rua=mailto:{MAILBOX}"]})
    step = steps_by_key(run())["tlsrpt"]
    assert step.status == "warn"
    assert step.record["value"].endswith(TLSRPT_MAILBOX)


def test_la_casse_de_tlsrpt_est_normative(monkeypatch):
    """RFC 8460 §3 définit la version en octets hexadécimaux : « v=TLSRPTV1 » est
    invalide. Erreur réellement commise en production."""
    stub(monkeypatch, txt={
        "exemple.com._report._smtp._tls.lsiparis.tech": ["v=TLSRPTV1"]})
    step = steps_by_key(run())["tlsrpt_auth"]
    assert step.status == "warn"
    assert "casse est NORMATIVE" in step.detail


def test_hote_mta_sts_pointant_ailleurs_est_signale(monkeypatch):
    stub(monkeypatch, a=["198.51.100.7"])
    step = steps_by_key(run())["mta_sts_host"]
    assert step.status == "warn"
    assert STS_IP in step.detail and "198.51.100.7" in step.detail


def test_le_domaine_qui_heberge_la_boite_n_a_pas_d_autorisation_a_poser(monkeypatch):
    """L'autorisation croisée n'existe que si la destination est EXTERNE. Le domaine de
    messagerie de la plateforme héberge la boîte : il n'a personne à qui demander, et
    réclamer l'enregistrement serait faux."""
    stub(monkeypatch)
    by = steps_by_key(run(REPORTING))

    for key in ("dmarc_auth", "tlsrpt_auth"):
        assert by[key].status == "ok"
        assert "sans objet" in by[key].detail
        assert by[key].record is None      # rien à créer
