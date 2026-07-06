from fastapi import HTTPException, Request, status

from app.db.session import tenant_scoped_session


def get_tenant_ctx(request: Request):
    return request.state.tenant


def get_db(request: Request):
    """Dépendance FastAPI : session déjà scopée au tenant de la requête.
    Toute route qui l'utilise est isolée par construction (RLS)."""
    ctx = request.state.tenant
    with tenant_scoped_session(tenant_id=ctx.active_tenant, bypass=ctx.bypass) as s:
        yield s


def require_role(*allowed: str):
    def _dep(request: Request):
        ctx = request.state.tenant
        if ctx.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                f"Rôle requis: {allowed}, obtenu: {ctx.role}")
        return ctx
    return _dep
