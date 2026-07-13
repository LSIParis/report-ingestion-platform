"""Le poller IMAP ne doit JAMAIS se fier au drapeau \\Seen.

Constaté en production : trois rapports DMARC dormaient dans la boîte depuis mai,
marqués « lus » par un client tiers (un webmail ouvert une fois suffit). Le poller ne
récupérait que les messages non lus : ils n'auraient JAMAIS été ingérés, sans la
moindre erreur ni alerte. C'est le pire type de perte — silencieuse et définitive.

La file d'attente, c'est le DOSSIER : ce qui reste dans la boîte reste à traiter, ce qui
est traité part dans Processed/.
"""
from dataclasses import dataclass, field

from app.ingestion.imap_client import PROCESSED_FOLDER, ImapPoller
from app.ingestion.service import IngestResult


@dataclass
class FakeMessage:
    uid: str
    seen: bool

    @property
    def obj(self):
        class _Obj:
            @staticmethod
            def as_bytes():
                return b"From: x@y.z\r\nSubject: s\r\nMessage-ID: <m>\r\n\r\ncorps"
        return _Obj()


@dataclass
class FakeFolder:
    existing: set = field(default_factory=lambda: {"INBOX"})

    def exists(self, name):
        return name in self.existing

    def create(self, name):
        self.existing.add(name)


@dataclass
class FakeMailbox:
    messages: list
    folder: FakeFolder = field(default_factory=FakeFolder)
    moved: list = field(default_factory=list)
    last_criteria: object = None
    last_mark_seen: object = None

    def fetch(self, criteria, mark_seen=True, bulk=False):
        self.last_criteria, self.last_mark_seen = criteria, mark_seen
        return list(self.messages)      # la boîte entière : c'est bien le point

    def move(self, uid, dest):
        self.moved.append((uid, dest))
        self.messages = [m for m in self.messages if m.uid != uid]


class FakeService:
    def __init__(self, status="enqueued"):
        self.status = status
        self.ingested = 0

    def ingest(self, raw_eml, source):
        self.ingested += 1
        return IngestResult(email_id="e1", status=self.status)


def poller(service):
    return ImapPoller(service, host="h", user="u", password="p")


def test_un_message_deja_lu_est_quand_meme_ingere():
    """LE test. Un rapport marqué \\Seen par un tiers doit être traité."""
    svc = FakeService()
    mb = FakeMailbox(messages=[FakeMessage(uid="4", seen=True),
                               FakeMessage(uid="5", seen=True)])

    assert poller(svc).process(mb) == 2
    assert svc.ingested == 2
    assert mb.moved == [("4", PROCESSED_FOLDER), ("5", PROCESSED_FOLDER)]


def test_le_poller_ne_marque_pas_les_messages_comme_lus():
    svc = FakeService()
    mb = FakeMailbox(messages=[FakeMessage(uid="1", seen=False)])
    poller(svc).process(mb)
    assert mb.last_mark_seen is False


def test_un_doublon_est_quand_meme_sorti_de_la_boite():
    """Sinon un message déjà connu resterait indéfiniment en boîte, rejoué à chaque
    passage : la boîte ne se viderait jamais."""
    svc = FakeService(status="duplicate")
    mb = FakeMailbox(messages=[FakeMessage(uid="9", seen=True)])

    assert poller(svc).process(mb) == 1
    assert mb.moved == [("9", PROCESSED_FOLDER)]


def test_un_message_invalide_reste_en_boite():
    """On préfère un message coincé et visible à un message perdu en silence."""
    svc = FakeService(status="invalid")
    mb = FakeMailbox(messages=[FakeMessage(uid="7", seen=False)])

    assert poller(svc).process(mb) == 0
    assert mb.moved == []
    assert [m.uid for m in mb.messages] == ["7"]


def test_le_dossier_processed_est_cree_si_absent():
    mb = FakeMailbox(messages=[])
    poller(FakeService()).process(mb)
    assert mb.folder.exists(PROCESSED_FOLDER)
