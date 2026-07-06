from fastapi import APIRouter, Depends, HTTPException, status

from app.api.pagination import Page, page_params, paginate
from app.api.schemas import AssignTenantIn, EmailOut
from app.auth.deps import get_db, get_tenant_ctx, require_role
from app.db.models import Email
from app.db.session import tenant_scoped_session
from app.services.audit import audit
from app.workers.tasks import process_email

router = APIRouter(prefix="/emails", tags=["emails"])


@router.get("", response_model=Page)
def list_emails(status_f: str | None = None, db=Depends(get_db), pg=Depends(page_params)):
    q = db.query(Email)
    if status_f:
        q = q.filter(Email.status == status_f)
    return paginate(q.order_by(Email.received_at.desc()), *pg)


@router.get("/queue/quarantine", response_model=Page,
            dependencies=[Depends(require_role("platform_admin"))])
def list_quarantine(pg=Depends(page_params)):
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        q = db.query(Email).filter(Email.tenant_id.is_(None),
                                   Email.status == "needs_review")
        return paginate(q.order_by(Email.received_at.desc()), *pg)


@router.get("/{email_id}", response_model=EmailOut)
def get_email(email_id: str, db=Depends(get_db)):
    em = db.get(Email, email_id)
    if not em:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "E-mail introuvable")
    return em


@router.post("/{email_id}/assign-tenant", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(require_role("platform_admin"))])
def assign_tenant(email_id: str, body: AssignTenantIn, ctx=Depends(get_tenant_ctx)):
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        em = db.get(Email, email_id)
        if not em:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "E-mail introuvable")
        if em.status != "needs_review":
            raise HTTPException(status.HTTP_409_CONFLICT, "E-mail non en quarantaine")
        em.tenant_id = body.tenant_id
        em.status = "tenant_resolved"
        em.resolved_by = "manual"
        db.commit()
    process_email.delay(email_id)
    audit(actor=ctx.user, action="email.assigned_manually",
          target_id=email_id, tenant_id=body.tenant_id)
    return {"status": "queued", "tenant_id": body.tenant_id}
