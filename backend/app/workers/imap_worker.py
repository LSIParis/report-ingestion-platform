from app.config import settings
from app.ingestion.imap_client import ImapPoller
from app.ingestion.service import IngestionService
from app.storage import ObjectStore


def main() -> None:
    store = ObjectStore.from_settings(settings)
    service = IngestionService(store)
    ImapPoller(
        service,
        host=settings.imap_host,
        user=settings.imap_user,
        password=settings.imap_password,
        interval_s=45,
    ).run_forever()


if __name__ == "__main__":
    main()
