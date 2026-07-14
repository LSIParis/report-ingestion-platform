"""Normalisation des rapports TLS-RPT (RFC 8460).

L'adaptateur (`tests/test_tlsrpt_adapter.py`) prouve seulement le `ParseResult` brut.
C'est `NormalizationService.normalize()`, avec le profil `_default_tlsrpt_json`, qui
produit les lignes réellement écrites en base (`ReportRow.data`) — c'est sur CE
résultat-là que l'écran de posture TLS s'appuie.

Le piège que ces tests couvrent : l'adaptateur émet directement `report_date` /
`period_end` (mapping identité dans le profil), contrairement au DMARC XML où
l'adaptateur émet `date_begin` et le profil le renomme. Si l'un des deux mappings
était faux (source absente, cible dupliquée, `required` posé sur un champ propre à
un seul `kind`), la normalisation le révélerait : soit par un statut `partial`/`failed`
inattendu, soit par un `report_date` resté `None`.
"""
import json

from app.normalization.normalizer import NormalizationService
from app.normalization.profiles import load_profile
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


def _normalized(raw: bytes):
    parsed = get_adapter("tlsrpt_json").parse(raw, PROFILE)
    assert parsed.status == "ok"          # précondition : l'adaptateur seul doit déjà passer
    return NormalizationService().normalize(parsed, PROFILE)


def test_normalisation_ne_rejette_aucune_ligne():
    """LE piège du profil : si un champ propre à `summary` (ex. `successful_sessions`)
    était marqué `required`, toutes les lignes `failure` seraient rejetées — et
    inversement pour un champ propre à `failure`. La moitié du rapport disparaîtrait
    en silence (statut resterait 'partial', pas 'failed' : facile à ne pas remarquer)."""
    r = _normalized(_rapport())

    assert r.status == "ok"
    assert r.errors == []
    assert len(r.rows) == 2


def test_ligne_summary_normalisee():
    r = _normalized(_rapport())
    summary = next(x for x in r.rows if x["kind"] == "summary")

    assert summary["policy_domain"] == "exemple.fr"
    assert summary["reporter"] == "Google Inc."
    assert summary["mx_host"] == "*.mail.protection.outlook.com"
    assert summary["policy_type"] == "sts"

    assert summary["successful_sessions"] == 100
    assert summary["failed_sessions"] == 3
    assert isinstance(summary["successful_sessions"], int)
    assert isinstance(summary["failed_sessions"], int)


def test_ligne_failure_normalisee():
    r = _normalized(_rapport())
    failure = next(x for x in r.rows if x["kind"] == "failure")

    assert failure["kind"] == "failure"
    assert failure["result_type"] == "certificate-host-mismatch"
    assert failure["sending_mta_ip"] == "203.0.113.5"
    assert failure["receiving_mx_hostname"] == "mx-backup.exemple.fr"
    assert failure["failure_sessions"] == 3
    assert isinstance(failure["failure_sessions"], int)


def test_report_date_caste_en_date_iso():
    """C'est exactement le bug silencieux qu'aurait produit un mapping cassé :
    `report_date` resterait à `None` sans faire échouer la ligne (le champ n'est
    'que' TYPE_CAST-invalide s'il est absent, jamais MISSING_FIELD sur une valeur
    déjà bien formée). On vérifie donc la valeur réelle, pas seulement sa présence."""
    r = _normalized(_rapport())

    for row in r.rows:
        assert row["report_date"] == "2026-07-13"
        assert row["period_end"] == "2026-07-13"


def test_compteurs_disjoints_apres_normalisation():
    """`failed_sessions` (résumé) et `failure_sessions` (détail) comptent les MÊMES
    sessions sous deux angles. Après normalisation, la clé existe sur CHAQUE ligne
    (le field_mapping est appliqué uniformément) mais sa valeur est `None` là où le
    champ source est absent — pas seulement absente du dict. Un `SUM()` SQL sur
    `data->>'failed_sessions'` ignore les JSON `null`, donc les lignes `failure`
    n'y contribuent pas : le mapping ne fait PAS le double comptage que la RFC
    cherche à éviter en nommant différemment les deux compteurs.
    """
    r = _normalized(_rapport())
    summary = next(x for x in r.rows if x["kind"] == "summary")
    failure = next(x for x in r.rows if x["kind"] == "failure")

    # présent et non-None là où c'est le compteur "propre" à la ligne
    assert summary["failed_sessions"] == 3
    assert failure["failure_sessions"] == 3

    # la clé existe (field_mapping appliqué uniformément) mais la valeur est None,
    # pas simplement absente : c'est ce qui garantit qu'un SUM() SQL l'ignore.
    assert "failure_sessions" in summary
    assert summary["failure_sessions"] is None
    assert "failed_sessions" in failure
    assert failure["failed_sessions"] is None


def test_rapport_sans_aucun_echec_normalise_sans_ligne_failure():
    """Rapport 100% propre : aucune ligne `failure`, donc aucun champ 'failure' à
    vérifier — juste que la ligne `summary` seule reste 'ok'."""
    r = get_adapter("tlsrpt_json").parse(_rapport(policies=[{
        "policy": {"policy-type": "sts", "policy-domain": "exemple.fr",
                   "mx-host": ["mx.exemple.fr"]},
        "summary": {"total-successful-session-count": 5000,
                    "total-failure-session-count": 0},
    }]), PROFILE)
    norm = NormalizationService().normalize(r, PROFILE)

    assert norm.status == "ok"
    assert len(norm.rows) == 1
    assert norm.rows[0]["kind"] == "summary"
    assert norm.rows[0]["successful_sessions"] == 5000
    assert norm.rows[0]["failed_sessions"] == 0
    assert norm.rows[0]["failure_sessions"] is None
