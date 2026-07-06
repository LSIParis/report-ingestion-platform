from fastapi import APIRouter, Depends, Query
from sqlalchemy import func

from app.api.schemas import MetricsSummaryOut
from app.auth.deps import get_db
from app.db.models import Email, Report

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/summary", response_model=MetricsSummaryOut)
def summary(db=Depends(get_db)):
    by_status = dict(db.query(Report.status, func.count()).group_by(Report.status).all())
    needs_review = (db.query(func.count()).select_from(Email)
                      .filter(Email.status == "needs_review").scalar())
    return MetricsSummaryOut(
        total=sum(by_status.values()),
        parsed_ok=by_status.get("ok", 0),
        parsed_partial=by_status.get("partial", 0),
        failed=by_status.get("failed", 0),
        needs_review=needs_review or 0,
    )


@router.get("/timeseries")
def timeseries(granularity: str = Query("day", pattern="^(day|week|month)$"),
               db=Depends(get_db)):
    bucket = func.date_trunc(granularity, Report.created_at).label("bucket")
    rows = (db.query(bucket, Report.status, func.count())
              .group_by(bucket, Report.status).order_by(bucket).all())
    return [{"bucket": b.isoformat(), "status": s, "count": c} for b, s, c in rows]


@router.get("/by-brand")
def by_brand(db=Depends(get_db)):
    rows = (db.query(Email.from_address, Report.status, func.count())
              .join(Report, Report.email_id == Email.id)
              .group_by(Email.from_address, Report.status).all())
    return [{"brand": f, "status": s, "count": c} for f, s, c in rows]
