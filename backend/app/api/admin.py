from fastapi import APIRouter, Depends

from app.auth.deps import require_role
from app.db.models import Tenant, TenantMatchingRule
from app.db.session import tenant_scoped_session

router = APIRouter(prefix="/admin", tags=["admin"],
                   dependencies=[Depends(require_role("platform_admin"))])


@router.get("/tenants")
def list_tenants():
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        return [{"id": str(t.id), "domain": t.domain, "name": t.name}
                for t in db.query(Tenant).order_by(Tenant.name).all()]


@router.get("/tenants/{tenant_id}/matching-rules")
def list_rules(tenant_id: str):
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        return [{"id": str(r.id), "tenant_id": str(r.tenant_id), "rule_type": r.rule_type,
                 "pattern": r.pattern, "priority": r.priority, "is_active": r.is_active}
                for r in db.query(TenantMatchingRule).filter_by(tenant_id=tenant_id)
                           .order_by(TenantMatchingRule.priority).all()]


@router.post("/tenants/{tenant_id}/matching-rules", status_code=201)
def add_rule(tenant_id: str, rule_type: str, pattern: str, priority: int = 100):
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        r = TenantMatchingRule(tenant_id=tenant_id, rule_type=rule_type,
                               pattern=pattern, priority=priority, is_active=True)
        db.add(r)
        db.commit()
        return {"id": str(r.id)}
