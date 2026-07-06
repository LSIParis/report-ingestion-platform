from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import settings

# Plan UTILISATEUR : rôle app_api (non-propriétaire, pas de bypass) → soumis à la RLS
_api_engine = create_engine(settings.database_url_api, pool_pre_ping=True)
ApiSession = sessionmaker(bind=_api_engine, expire_on_commit=False)

# Plan SYSTÈME : rôle app_worker (BYPASSRLS) pour l'ingestion/parsing cross-tenant
_worker_engine = create_engine(settings.database_url_worker, pool_pre_ping=True)
WorkerSession = sessionmaker(bind=_worker_engine, expire_on_commit=False)


@contextmanager
def get_session():
    """Plan système (worker). Bypass RLS assumé."""
    s = WorkerSession()
    try:
        yield s
    finally:
        s.close()


@contextmanager
def tenant_scoped_session(*, tenant_id: str | None, bypass: bool = False):
    """
    Plan utilisateur. Ouvre UNE transaction, y pose le contexte via SET LOCAL
    (transaction-local → aucune fuite entre requêtes du pool), puis commit/rollback.
    """
    s = ApiSession()
    try:
        s.begin()
        if bypass:
            s.execute(text("SET LOCAL app.bypass_tenant = 'on'"))
        else:
            s.execute(text("SELECT set_config('app.current_tenant', :tid, true)"),
                      {"tid": str(tenant_id) if tenant_id else ""})
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
