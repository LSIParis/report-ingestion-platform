"""Rapports TLS-RPT (RFC 8460).

Deux tests portent le poids :
 - `test_pas_de_double_comptage_des_echecs` : le résumé et le détail comptent les MÊMES
   sessions. S'ils partageaient un nom de champ, un SUM() sur la table les additionnerait
   et la statistique la plus regardée de l'écran serait fausse, en silence.
 - `test_rapport_pour_un_autre_domaine_est_rejete` : le garde anti-usurpation existant
   doit couvrir ce format sans modification. Le test le PROUVE, il ne le suppose pas.
"""
import gzip
import json

from app.normalization.profiles import load_profile
from app.parsing.guards import guard_report_domain
from app.parsing.registry import get_adapter

PROFILE = load_profile("_default_tlsrpt_json")


def _rapport(policies=None, domain="exemple.fr") -> bytes:
    return json.dumps({
        "organization-name": "Google Inc.",
        "date-range": {"start-datetime": "2026-07-13T00:00:00Z",
                       "end-datetime": "2026-07-13T23:59:59Z"},
        "contact-info": "smtp-tls-reporting@google.com",
        "report-id": "2026-07-13T00:00:00Z_exemple.fr",
        "policies": policies if policies is not None else [{
            "policy": {"policy-type": "sts",
                       "policy-string": ["version: STSv1", "mode: testing"],
                       "policy-domain": domain,
                       "mx-host": ["*.mail.protection.outlook.com"]},
            "summary": {"total-successful-session-count": 100,
                        "total-failure-session-count": 3},
            "failure-details": [{
                "result-type": "certificate-host-mismatch",
                "sending-mta-ip": "203.0.113.5",
                "receiving-mx-hostname": "mx-backup.exemple.fr",
                "receiving-ip": "198.51.100.7",
                "failed-session-count": 3,
            }],
        }],
    }).encode()


def _parse(raw: bytes):
    return get_adapter("tlsrpt_json").parse(raw, PROFILE)


def test_rapport_nominal():
    r = _parse(_rapport())

    assert r.status == "ok"
    assert r.metadata["policy_domain"] == "exemple.fr"

    summary = [x for x in r.rows if x["kind"] == "summary"]
    failure = [x for x in r.rows if x["kind"] == "failure"]
    assert len(summary) == 1 and len(failure) == 1

    assert summary[0]["successful_sessions"] == 100
    assert summary[0]["failed_sessions"] == 3
    assert summary[0]["reporter"] == "Google Inc."
    assert summary[0]["report_date"] == "2026-07-13"
    assert summary[0]["mx_host"] == "*.mail.protection.outlook.com"

    assert failure[0]["result_type"] == "certificate-host-mismatch"
    assert failure[0]["sending_mta_ip"] == "203.0.113.5"
    assert failure[0]["receiving_mx_hostname"] == "mx-backup.exemple.fr"
    assert failure[0]["failure_sessions"] == 3


def test_pas_de_double_comptage_des_echecs():
    """Le résumé dit « 3 échecs ». Le détail dit « 3 sessions échouées ». Ce sont LES
    MÊMES. Les champs portent donc des noms distincts, pour qu'un SUM() naïf ne puisse
    pas les additionner."""
    rows = _parse(_rapport()).rows

    summary = next(x for x in rows if x["kind"] == "summary")
    failure = next(x for x in rows if x["kind"] == "failure")

    assert "failed_sessions" in summary and "failure_sessions" not in summary
    assert "failure_sessions" in failure and "failed_sessions" not in failure


def test_rapport_sans_aucun_echec():
    r = _parse(_rapport(policies=[{
        "policy": {"policy-type": "sts", "policy-domain": "exemple.fr",
                   "mx-host": ["mx.exemple.fr"]},
        "summary": {"total-successful-session-count": 5000,
                    "total-failure-session-count": 0},
    }]))

    assert r.status == "ok"
    assert len(r.rows) == 1
    assert r.rows[0]["kind"] == "summary"
    assert r.rows[0]["successful_sessions"] == 5000
    assert r.rows[0]["failed_sessions"] == 0


def test_rapport_compresse():
    r = _parse(gzip.compress(_rapport()))
    assert r.status == "ok"


def test_json_malforme():
    r = _parse(b"{ ceci n est pas du json")
    assert r.status == "failed"
    assert r.errors[0]["code"] == "TLSRPT_BAD_JSON"


def test_sans_policy_domain_on_refuse_plutot_que_deviner():
    r = _parse(json.dumps({
        "organization-name": "X", "report-id": "y",
        "date-range": {"start-datetime": "2026-07-13T00:00:00Z",
                       "end-datetime": "2026-07-13T23:59:59Z"},
        "policies": [{"policy": {"policy-type": "sts"},
                      "summary": {"total-successful-session-count": 1,
                                  "total-failure-session-count": 0}}],
    }).encode())
    assert r.status == "failed"
    assert r.errors[0]["code"] == "TLSRPT_NO_POLICY_DOMAIN"


def test_result_type_inconnu_est_conserve_tel_quel():
    """La RFC évoluera. On ne traduit pas, on ne devine pas : on garde ce qui est écrit."""
    r = _parse(_rapport(policies=[{
        "policy": {"policy-type": "sts", "policy-domain": "exemple.fr", "mx-host": []},
        "summary": {"total-successful-session-count": 0,
                    "total-failure-session-count": 1},
        "failure-details": [{"result-type": "quelque-chose-de-nouveau",
                             "failed-session-count": 1}],
    }]))

    failure = next(x for x in r.rows if x["kind"] == "failure")
    assert failure["result_type"] == "quelque-chose-de-nouveau"


def test_politique_corrompue_parmi_d_autres_ne_perd_pas_les_bonnes():
    r = _parse(_rapport(policies=[
        {"policy": {"policy-type": "sts", "policy-domain": "exemple.fr", "mx-host": []},
         "summary": {"total-successful-session-count": 10,
                     "total-failure-session-count": 0}},
        {"policy": {"policy-type": "sts", "policy-domain": "exemple.fr"},
         "summary": "ceci devrait être un objet"},
    ]))

    assert r.status == "partial"
    assert any(x["kind"] == "summary" and x["successful_sessions"] == 10 for x in r.rows)
    assert any(e["code"] == "TLSRPT_BAD_POLICY" for e in r.errors)


def test_rapport_pour_un_autre_domaine_est_rejete():
    """Le sujet du mail est contrôlé par l'expéditeur : n'importe qui peut forger
    « Report Domain: client-a.com ». Le garde existant recoupe le CONTENU — et il doit
    couvrir TLS-RPT sans une ligne de code en plus."""
    parsed = _parse(_rapport(domain="victime.com"))

    garde = guard_report_domain(parsed, "exemple.fr")

    assert garde.status == "failed"
    assert garde.rows == []
