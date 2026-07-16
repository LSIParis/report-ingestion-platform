"""API publique v1 — surface stable pour programmes tiers (clés API).

Lectures scopées par la session (`get_db`) : une clé domaine ne voit que son tenant via
la RLS. La table `tenant` n'ayant PAS de RLS, on la filtre explicitement quand la session
n'est pas en bypass. Les agrégats réutilisent les helpers de `metrics` (pas de duplication).
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func

from app.api import metrics as metrics_api
from app.api.admin import TenantIn  # réutilise la validation de domaine
from app.auth.deps import get_db, get_tenant_ctx
from app.db.models import Email, Report, Tenant
from app.db.session import tenant_scoped_session
from app.services.audit import audit
from app.services.tenants import ensure_tenant

router = APIRouter(prefix="/v1", tags=["public"])


def require_platform(ctx=Depends(get_tenant_ctx)):
    """Autorise une clé plateforme, ou un admin JWT en vue globale. Sinon 403."""
    is_platform_key = ctx.api_key_scope == "platform"
    is_admin_user = ctx.api_key_scope is None and ctx.role == "platform_admin"
    if not (is_platform_key or is_admin_user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "réservé aux clés plateforme")
    return ctx


@router.get("/domains")
def list_domains(db=Depends(get_db), ctx=Depends(get_tenant_ctx)):
    stats = dict(
        (tid, (n, last)) for tid, n, last in
        db.query(Report.tenant_id, func.count(Report.id), func.max(Report.created_at))
          .group_by(Report.tenant_id).all()
    )
    q = db.query(Tenant)
    # tenant n'a pas de RLS : une session scopée doit filtrer explicitement sur son tenant.
    if not ctx.bypass and ctx.active_tenant:
        q = q.filter(Tenant.id == ctx.active_tenant)
    out = []
    for t in q.order_by(Tenant.domain).all():
        reports, last = stats.get(t.id, (0, None))
        out.append({
            "id": str(t.id), "domain": t.domain, "name": t.name, "status": t.status,
            "reports": reports, "last_report_at": last.isoformat() if last else None,
            "alert_email": t.alert_email,
        })
    return out


@router.get("/reports")
def reports_summary(days: int = Query(30, ge=1, le=365), db=Depends(get_db)):
    """Agrégats DMARC sur la fenêtre (réutilise metrics.dmarc_summary, scopé par la session)."""
    return metrics_api.dmarc_summary(days=days, db=db)


@router.get("/metrics")
def metrics_timeseries(days: int = Query(30, ge=1, le=365), db=Depends(get_db)):
    """Série quotidienne conforme/échoué (réutilise metrics.dmarc_timeseries)."""
    return metrics_api.dmarc_timeseries(days=days, db=db)


@router.get("/quarantine", dependencies=[Depends(require_platform)])
def quarantine():
    """Rapports non attribués (tenant_id NULL, needs_review). Cross-tenant → plateforme."""
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        rows = (db.query(Email)
                  .filter(Email.tenant_id.is_(None), Email.status == "needs_review")
                  .order_by(Email.received_at.desc()).limit(500).all())
        return [{"id": str(e.id), "message_id": e.message_id, "from_address": e.from_address,
                 "subject": e.subject,
                 "received_at": e.received_at.isoformat() if e.received_at else None}
                for e in rows]


@router.post("/domains", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_platform)])
def create_domain(body: TenantIn, ctx=Depends(get_tenant_ctx)):
    """Crée un domaine (= un tenant). Même logique que POST /admin/tenants."""
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        if db.query(Tenant).filter_by(domain=body.domain).first():
            raise HTTPException(status.HTTP_409_CONFLICT, "Ce domaine est déjà surveillé")
        tenant, _ = ensure_tenant(db, body.domain, body.name)
        out = {"id": str(tenant.id), "domain": tenant.domain, "name": tenant.name}
        db.commit()

    audit(actor=ctx.user, action="tenant.created", target_id=out["id"],
          metadata={"domain": out["domain"], "via": "api_v1"})
    return out
