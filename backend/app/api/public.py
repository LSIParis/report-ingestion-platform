"""API publique v1 — surface stable pour programmes tiers (clés API).

Lectures scopées par la session (`get_db`) : une clé domaine ne voit que son tenant via
la RLS. La table `tenant` n'ayant PAS de RLS, on la filtre explicitement quand la session
n'est pas en bypass. Les agrégats réutilisent les helpers de `metrics` (pas de duplication).
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func

from app.api import metrics as metrics_api
from app.auth.deps import get_db, get_tenant_ctx
from app.db.models import Report, Tenant

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
