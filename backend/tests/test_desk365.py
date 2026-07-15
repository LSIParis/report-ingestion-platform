"""Le canal Desk365. L'API est moquee via `_call` : ces tests prouvent la LOGIQUE
(quand creer un ticket, quand annoter, quand s'abstenir), pas le format sur le fil (ca,
c'est le test reel de la tache 4).
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.services.alerting.channels import desk365
from app.services.alerting.channels.base import CanalIndisponible


class _Alert:
    def __init__(self, severity="critical", kind="never_reported",
                 external_ref=None, closed_at=None):
        self.id = uuid.uuid4()
        self.kind = kind
        self.severity = severity
        self.dedup_key = ""
        self.payload = {"domain": "client.fr", "age_days": 30}
        self.opened_at = datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)
        self.closed_at = closed_at
        self.external_ref = external_ref


class _Tenant:
    id = uuid.uuid4()
    domain = "client.fr"


@pytest.fixture(autouse=True)
def _configure(monkeypatch):
    monkeypatch.setattr(desk365.settings, "desk365_api_key", "cle-de-test")
    monkeypatch.setattr(desk365.settings, "desk365_base_url",
                        "https://lsi-maintenance.desk365.io/apis/v3")


def _mock_call(monkeypatch, resp=None, capture=None):
    def fake(method, path, body):
        if capture is not None:
            capture.append((method, path, body))
        return resp or {}
    monkeypatch.setattr(desk365, "_call", fake)


def test_ouverture_critique_cree_un_ticket_et_pose_external_ref(monkeypatch):
    calls = []
    _mock_call(monkeypatch, resp={"ticket_number": "TCK-777"}, capture=calls)
    a = _Alert(severity="critical")

    envoye = desk365.envoyer("opened", a, _Tenant())

    assert envoye is True
    assert a.external_ref == "TCK-777"
    method, path, body = calls[0]
    assert method == "POST" and "create" in path
    assert body["contact_email"] == "alerte_dmarc@lsiparis.tech"
    assert body["group"] == "Support informatique"
    assert body["priority"] == 20
    assert body["category"] == "Réseau"
    assert body["sub_category"] == "Déliverabilité emails"
    assert "client.fr" in body["subject"]


def test_ouverture_avertissement_ne_cree_rien(monkeypatch):
    calls = []
    _mock_call(monkeypatch, capture=calls)
    a = _Alert(severity="warning")

    envoye = desk365.envoyer("opened", a, _Tenant())

    assert envoye is False
    assert a.external_ref is None
    assert calls == []                      # aucun appel API


def test_fermeture_avec_ticket_ajoute_une_note(monkeypatch):
    calls = []
    _mock_call(monkeypatch, capture=calls)
    a = _Alert(external_ref="TCK-777",
               closed_at=datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc))

    envoye = desk365.envoyer("closed", a, _Tenant())

    assert envoye is True
    method, path, body = calls[0]
    assert method == "POST" and "note" in path.lower()
    assert "TCK-777" in str(body.values())    # le ticket est reference
    # on annote, on ne cloture pas : aucun changement de statut demande, juste une note
    joint = " ".join(str(v) for v in body.values())
    assert "résolue" in joint and "clôturez" in joint


def test_fermeture_sans_ticket_ne_fait_rien(monkeypatch):
    calls = []
    _mock_call(monkeypatch, capture=calls)
    a = _Alert(external_ref=None,
               closed_at=datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc))

    envoye = desk365.envoyer("closed", a, _Tenant())

    assert envoye is False
    assert calls == []


def test_non_configure_ne_leve_pas_mais_ne_se_tait_pas(monkeypatch):
    monkeypatch.setattr(desk365.settings, "desk365_api_key", "")

    envoye = desk365.envoyer("opened", _Alert(), _Tenant())

    assert envoye is False                   # rien fait, mais journalise (voir le module)


def test_reponse_sans_numero_de_ticket_leve(monkeypatch):
    """Sans numero exploitable, on ne peut pas suivre le ticket : on retente plutot que
    de poser un external_ref faux."""
    _mock_call(monkeypatch, resp={"message": "created but weird"})
    a = _Alert(severity="critical")

    with pytest.raises(CanalIndisponible):
        desk365.envoyer("opened", a, _Tenant())
    assert a.external_ref is None


def test_panne_api_leve_canal_indisponible(monkeypatch):
    def casse(method, path, body):
        raise CanalIndisponible("500")
    monkeypatch.setattr(desk365, "_call", casse)

    with pytest.raises(CanalIndisponible):
        desk365.envoyer("opened", _Alert(severity="critical"), _Tenant())
