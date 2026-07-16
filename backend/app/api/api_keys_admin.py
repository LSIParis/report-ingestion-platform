"""Gestion des clés API (admin JWT uniquement — hors /api/v1, donc inatteignable par une clé)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth.deps import get_tenant_ctx, require_role
from app.db.models import ApiKey, Tenant
from app.db.session import tenant_scoped_session
from app.services.api_keys import generate_key
from app.services.audit import audit

router = APIRouter(prefix="/admin", tags=["admin"],
                   dependencies=[Depends(require_role("platform_admin"))])


class ApiKeyIn(BaseModel):
    scope: str                     # 'platform' | 'domain'
    tenant_id: str | None = None
    label: str = ""


def _row(k: ApiKey, domain: str | None) -> dict:
    return {"id": str(k.id), "scope": k.scope,
            "tenant_id": str(k.tenant_id) if k.tenant_id else None, "domain": domain,
            "prefix": k.prefix, "label": k.label,
            "created_at": k.created_at.isoformat() if k.created_at else None,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "revoked_at": k.revoked_at.isoformat() if k.revoked_at else None}


@router.post("/api-keys", status_code=status.HTTP_201_CREATED)
def create_api_key(body: ApiKeyIn, ctx=Depends(get_tenant_ctx)):
    if body.scope not in ("platform", "domain"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "scope invalide")
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        tenant = None
        if body.scope == "domain":
            if not body.tenant_id:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "tenant_id requis (scope domaine)")
            tenant = db.get(Tenant, body.tenant_id)
            if not tenant:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "domaine introuvable")
        elif body.tenant_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "tenant_id interdit (scope plateforme)")

        secret, prefix, key_hash = generate_key(body.scope)
        key = ApiKey(scope=body.scope, tenant_id=(tenant.id if tenant else None),
                     prefix=prefix, key_hash=key_hash, label=body.label or body.scope,
                     created_by=ctx.user)
        db.add(key)
        db.flush()
        out = _row(key, tenant.domain if tenant else None)
        db.commit()
    audit(actor=ctx.user, action="api_key.created", target_id=out["id"],
          metadata={"scope": out["scope"], "domain": out["domain"]})
    # Le secret n'apparaît QUE dans cette réponse de création — jamais relisté ensuite.
    return {**out, "secret": secret}


@router.get("/api-keys")
def list_api_keys():
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        domains = dict(db.query(Tenant.id, Tenant.domain).all())
        return [_row(k, domains.get(k.tenant_id))
                for k in db.query(ApiKey).order_by(ApiKey.created_at.desc()).all()]


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(key_id: str, ctx=Depends(get_tenant_ctx)):
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        key = db.get(ApiKey, key_id)
        if key and key.revoked_at is None:      # idempotent : déjà révoquée → no-op
            key.revoked_at = datetime.now(timezone.utc)
            db.commit()
            audit(actor=ctx.user, action="api_key.revoked", target_id=key_id)
