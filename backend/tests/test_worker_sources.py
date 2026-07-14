"""`_list_sources` (backend/app/workers/tasks.py) : deux défauts trouvés à la relecture
du commit 63b34e1 (détection du format par le contenu, plus par l'extension).

1. L'antivirus doit scanner AVANT toute décompression (`detect_format` décompresse
   gzip/zip pour renifler le contenu — décompresser un flux hostile non scanné est une
   forme de parsing, interdit par CLAUDE.md : « antivirus avant tout stockage/parsing »).

2. Une pièce jointe qui RESSEMBLE à un rapport (extension .gz/.zip/.xml/.json, ou pas
   d'extension) mais qu'on n'arrive pas à décoder ne doit plus disparaître en silence :
   elle doit laisser un Report(status="failed") + ParsingError(code=ATTACHMENT_UNREADABLE),
   calqué sur ce que `_record_infected` fait déjà pour une pièce virale — mais en gardant
   le fichier stocké, puisqu'il n'est pas dangereux.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart

import pytest

from app.db.models import Attachment, Email, ParsingError, Report, Tenant
from app.db.session import get_session
from app.services import antivirus
from app.workers import tasks


class FakeStore:
    """Remplace l'object store réel : pas besoin de MinIO pour ce test, seulement de
    vérifier ce que le worker DÉCIDE de faire (scanner, stocker, ignorer, tracer)."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def get_default(self, key: str) -> bytes:
        return self.objects[key]

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.objects[key] = data


def _raw_eml(filename: str, payload: bytes) -> bytes:
    msg = MIMEMultipart()
    msg["From"] = "reports@tenant-test.com"
    msg["Subject"] = "rapport"
    msg["Message-ID"] = f"<{uuid.uuid4()}>"
    part = MIMEApplication(payload, Name=filename)
    part.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(part)
    return msg.as_bytes()


@pytest.fixture
def tenant_and_email(monkeypatch):
    """Un tenant + un email dont le .eml brut est servi par un FakeStore, pas MinIO.
    Renvoie (tenant_id, email_id, fake_store) ; nettoie la DB ensuite."""
    fake_store = FakeStore()
    monkeypatch.setattr(tasks, "store", fake_store)

    with get_session() as db:
        t = Tenant(domain=f"worker-test-{uuid.uuid4().hex[:8]}.com", name="Worker Test")
        db.add(t)
        db.flush()

        raw_key = f"raw/{uuid.uuid4()}.eml"
        em = Email(
            tenant_id=t.id,
            message_id=f"test-{uuid.uuid4()}",
            from_address="reports@tenant-test.com",
            subject="rapport",
            received_at=datetime.now(timezone.utc),
            raw_object_key=raw_key,
            status="processing",
        )
        db.add(em)
        db.flush()
        tenant_id, email_id = str(t.id), str(em.id)
        db.commit()

    yield tenant_id, email_id, fake_store

    with get_session() as db:
        report_ids = [r.id for r in db.query(Report.id).filter_by(email_id=email_id).all()]
        if report_ids:
            db.query(ParsingError).filter(ParsingError.report_id.in_(report_ids)).delete(
                synchronize_session=False)
            db.query(Report).filter(Report.id.in_(report_ids)).delete(synchronize_session=False)
        db.query(Attachment).filter_by(email_id=email_id).delete(synchronize_session=False)
        db.query(Email).filter_by(id=email_id).delete(synchronize_session=False)
        db.query(Tenant).filter_by(id=tenant_id).delete(synchronize_session=False)
        db.commit()


def test_piece_jointe_qui_ressemble_a_un_rapport_mais_illisible_laisse_une_trace(
        tenant_and_email, monkeypatch):
    """AVANT ce correctif : detect_format échouait à décompresser, renvoyait None,
    et la pièce disparaissait — aucun Attachment, aucun Report, aucune ParsingError."""
    monkeypatch.setattr(antivirus.settings, "antivirus_enabled", False)

    tenant_id, email_id, fake_store = tenant_and_email
    filename = "google.com!exemple.fr!1!2.json.gz"
    payload = b"\x1f\x8bcasse"  # nombre magique gzip, contenu corrompu : indécompressable
    with get_session() as db:
        em = db.get(Email, email_id)
        raw_key = em.raw_object_key
    fake_store.objects[raw_key] = _raw_eml(filename, payload)

    sources, infected, unreadable = tasks._list_sources(email_id, tenant_id)

    assert sources == []
    assert infected == 0
    assert unreadable == 1

    with get_session() as db:
        reports = db.query(Report).filter_by(email_id=email_id).all()
        assert len(reports) == 1
        assert reports[0].status == "failed"

        errors = db.query(ParsingError).filter_by(email_id=email_id).all()
        assert len(errors) == 1
        assert errors[0].code == "ATTACHMENT_UNREADABLE"

        attachments = db.query(Attachment).filter_by(email_id=email_id).all()
        assert len(attachments) == 1
        # Contrairement à un virus : le fichier n'est pas dangereux, il reste stocké.
        assert attachments[0].object_key != "(infecté — non stocké)"
        assert fake_store.objects[attachments[0].object_key] == payload


def test_piece_jointe_hors_sujet_illisible_reste_ignoree_en_silence(
        tenant_and_email, monkeypatch):
    """Une extension qui ne nous intéresse pas (.txt) n'est pas un rapport : illisible ou
    pas, elle continue d'être ignorée sans laisser de trace."""
    monkeypatch.setattr(antivirus.settings, "antivirus_enabled", False)

    tenant_id, email_id, fake_store = tenant_and_email
    with get_session() as db:
        em = db.get(Email, email_id)
        raw_key = em.raw_object_key
    fake_store.objects[raw_key] = _raw_eml("notes.txt", b"bonjour")

    sources, infected, unreadable = tasks._list_sources(email_id, tenant_id)

    assert sources == []
    assert infected == 0
    assert unreadable == 0

    with get_session() as db:
        assert db.query(Report).filter_by(email_id=email_id).count() == 0
        assert db.query(ParsingError).filter_by(email_id=email_id).count() == 0
        assert db.query(Attachment).filter_by(email_id=email_id).count() == 0


def test_antivirus_scanne_avant_toute_decompression(tenant_and_email, monkeypatch):
    """LE test qui prouve l'ordre. `detect_format` est remplacé par un espion qui lève si
    on l'appelle : si l'antivirus s'exécute bien EN PREMIER et trouve un virus, on ne
    doit JAMAIS atteindre `detect_format` (donc jamais décompresser des octets non scannés)."""
    tenant_id, email_id, fake_store = tenant_and_email

    def _boom(payload, filename):
        raise AssertionError("detect_format ne doit pas etre appele avant l'antivirus")

    monkeypatch.setattr(tasks, "detect_format", _boom)
    monkeypatch.setattr(antivirus, "scan",
                        lambda payload: (_ for _ in ()).throw(antivirus.VirusFound("EICAR")))

    with get_session() as db:
        em = db.get(Email, email_id)
        raw_key = em.raw_object_key
    # Peu importe le contenu : detect_format ne doit jamais être appelé pour ce fichier.
    fake_store.objects[raw_key] = _raw_eml("rapport.xml", b"<xml/>")

    sources, infected, unreadable = tasks._list_sources(email_id, tenant_id)

    assert sources == []
    assert infected == 1
    assert unreadable == 0

    with get_session() as db:
        errors = db.query(ParsingError).filter_by(email_id=email_id).all()
        assert len(errors) == 1
        assert errors[0].code == "VIRUS_DETECTED"
