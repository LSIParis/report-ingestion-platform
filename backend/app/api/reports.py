from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func

from app.api.metrics import (
    _aligned, _dkim, _est_une_ligne_dmarc, _msgs, _msgs_where, _source_ip, _spf,
)
from app.api.pagination import Page, page_params, paginate
from app.api.schemas import ParsingErrorOut, ReportOut, ReportRowOut
from app.auth.deps import get_db, get_tenant_ctx
from app.config import settings
from app.db.models import Attachment, Email, ParsingError, Report, ReportRow
from app.services.audit import audit
from app.storage import ObjectStore
from app.workers.tasks import reprocess_report

router = APIRouter(prefix="/reports", tags=["reports"])
store = ObjectStore.from_settings(settings)


@router.get("", response_model=Page[ReportOut])
def list_reports(status_f: str | None = None, brand: str | None = None,
                 kind: str | None = None,
                 db=Depends(get_db), pg=Depends(page_params)):
    q = db.query(Report)
    if status_f:
        q = q.filter(Report.status == status_f)
    if kind:
        q = q.filter(Report.kind == kind)
    if brand:
        q = q.join(Email, Email.id == Report.email_id)\
             .filter(Email.from_address.ilike(f"%{brand}%"))
    return paginate(q.order_by(Report.created_at.desc()), *pg)


@router.get("/{report_id}", response_model=ReportOut)
def get_report(report_id: str, db=Depends(get_db)):
    r = db.get(Report, report_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rapport introuvable")
    return r


@router.get("/{report_id}/breakdown")
def get_report_breakdown(report_id: str, db=Depends(get_db)):
    """Analyse d'un rapport pour le bandeau et la vue groupee. Kind-aware.

    Tout passe par la session scopee (RLS) : un rapport d'un autre tenant -> 404 (via
    db.get qui ne le voit pas), jamais 403. Les casts JSONB sont ceux de metrics.py,
    ici FILTRES sur ce report_id.
    """
    r = db.get(Report, report_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rapport introuvable")

    # policy_domain : uniforme sur le rapport, lu sur la premiere ligne qui le porte.
    domain = (db.query(ReportRow.data["policy_domain"].astext)
                .filter(ReportRow.report_id == report_id,
                        ReportRow.data["policy_domain"].astext.isnot(None))
                .limit(1).scalar())
    out: dict = {"policy_domain": domain}

    if r.kind == "dmarc":
        agg = (db.query(
                   _msgs_where(_dkim == "pass").label("dkim_aligned"),
                   _msgs_where(_spf == "pass").label("spf_aligned"))
               .filter(ReportRow.report_id == report_id, _est_une_ligne_dmarc)
               .one())
        sources = (db.query(
                       _source_ip.label("ip"),
                       func.coalesce(func.sum(_msgs), 0).label("messages"),
                       _msgs_where(_aligned == "pass").label("compliant"))
                   .filter(ReportRow.report_id == report_id, _est_une_ligne_dmarc)
                   .group_by(_source_ip)
                   .order_by(func.coalesce(func.sum(_msgs), 0).desc())
                   .all())
        out["dkim_aligned"] = int(agg.dkim_aligned)
        out["spf_aligned"] = int(agg.spf_aligned)
        out["sources"] = [{"source_ip": ip, "messages": int(m),
                           "compliant": int(c), "failing": int(m) - int(c)}
                          for ip, m, c in sources]
    return out


@router.get("/{report_id}/rows", response_model=Page[ReportRowOut])
def get_report_rows(report_id: str, db=Depends(get_db), pg=Depends(page_params)):
    q = db.query(ReportRow).filter(ReportRow.report_id == report_id)
    return paginate(q.order_by(ReportRow.report_date), *pg)


@router.get("/{report_id}/errors", response_model=list[ParsingErrorOut])
def get_report_errors(report_id: str, db=Depends(get_db)):
    return (db.query(ParsingError)
              .filter(ParsingError.report_id == report_id)
              .order_by(ParsingError.severity.desc()).all())


@router.get("/{report_id}/raw")
def get_report_raw(report_id: str, db=Depends(get_db), ctx=Depends(get_tenant_ctx)):
    r = db.get(Report, report_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rapport introuvable")
    if r.attachment_id:
        key = db.get(Attachment, r.attachment_id).object_key
    else:
        key = db.get(Email, r.email_id).raw_object_key
    audit(actor=ctx.user, action="report.raw_downloaded",
          target_id=report_id, tenant_id=ctx.active_tenant)
    return {"url": store.presign_get(key, expires_s=300)}


@router.post("/{report_id}/reprocess", status_code=status.HTTP_202_ACCEPTED)
def reprocess(report_id: str, db=Depends(get_db), ctx=Depends(get_tenant_ctx)):
    r = db.get(Report, report_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rapport introuvable")
    reprocess_report.delay(str(r.email_id))
    audit(actor=ctx.user, action="report.reprocess",
          target_id=report_id, tenant_id=ctx.active_tenant)
    return {"status": "queued", "email_id": str(r.email_id)}
