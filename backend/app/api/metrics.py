from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Integer, cast, func

from app.api.schemas import MetricsSummaryOut
from app.auth.deps import get_db
from app.db.models import Email, Report, ReportRow

router = APIRouter(prefix="/metrics", tags=["metrics"])

# Toutes les requêtes passent par `get_db` : la session est déjà scopée au tenant et la
# RLS filtre en base. On n'ajoute JAMAIS de WHERE tenant_id applicatif.

# --- Accès aux champs DMARC, stockés en JSONB dans report_row.data ---------------
# `message_count` est le nombre de messages que le fournisseur a vus pour cette ligne :
# une ligne peut représenter 1 message comme 12 000. Compter les LIGNES n'aurait aucun
# sens métier — on somme donc toujours les messages.
_msgs = cast(ReportRow.data["message_count"].astext, Integer)
_aligned = ReportRow.data["aligned"].astext          # 'pass' si DKIM OU SPF est aligné
_dkim = ReportRow.data["dkim"].astext
_spf = ReportRow.data["spf"].astext
_disposition = ReportRow.data["disposition"].astext  # none | quarantine | reject
_source_ip = ReportRow.data["source_ip"].astext
_reporter = ReportRow.data["reporter"].astext

# `report_row` porte à la fois les lignes DMARC et les lignes TLS-RPT (même table,
# voulu). Le profil TLS (`_default_tlsrpt_json.json`) ne connaît ni `source_ip`, ni
# `message_count`, ni `aligned` : ces clés sont ABSENTES de `data` pour une ligne TLS,
# donc `data->>'source_ip'` y vaut toujours NULL. Le profil DMARC (`_default_dmarc_xml
# .json`), lui, marque `source_ip` `required`. C'est donc un critère fiable pour
# distinguer les deux familles. SANS ce filtre, les lignes TLS entrent quand même
# dans les GROUP BY source_ip : elles forment un groupe unique à l'IP NULL, compté
# comme une "source" qui n'authentifie jamais rien (aucune de ses colonnes DMARC
# n'existe) — une source fantôme dans le tableau de bord. Ne PAS supprimer ce filtre :
# il n'est pas redondant, même si aucune ligne TLS n'est visible dans un test donné.
_est_une_ligne_dmarc = _source_ip.isnot(None)


def _msgs_where(condition):
    """Somme des messages vérifiant une condition (0 sinon)."""
    return func.coalesce(func.sum(func.cast(condition, Integer) * _msgs), 0)


def _since(days: int):
    return ReportRow.report_date >= date.today() - timedelta(days=days)


# ---------------------------------------------------------------- santé du pipeline
@router.get("/summary", response_model=MetricsSummaryOut)
def summary(db=Depends(get_db)):
    """État de l'ingestion (exploitation), pas de la posture DMARC."""
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


# ------------------------------------------------------------------ posture DMARC
@router.get("/dmarc/summary")
def dmarc_summary(days: int = Query(30, ge=1, le=365), db=Depends(get_db)):
    """Chiffres clés sur la fenêtre : volume, conformité, sources."""
    row = db.query(
        func.coalesce(func.sum(_msgs), 0).label("messages"),
        _msgs_where(_aligned == "pass").label("compliant"),
        _msgs_where(_dkim == "pass").label("dkim_pass"),
        _msgs_where(_spf == "pass").label("spf_pass"),
        _msgs_where(_disposition == "quarantine").label("quarantined"),
        _msgs_where(_disposition == "reject").label("rejected"),
        func.count(func.distinct(_source_ip)).label("sources"),
    ).filter(_since(days), _est_une_ligne_dmarc).one()

    messages = int(row.messages)
    compliant = int(row.compliant)
    # Sources dont AUCUN message n'est authentifié : ce sont elles qu'il faut traiter.
    failing_sources = (
        db.query(_source_ip)
          .filter(_since(days), _est_une_ligne_dmarc)
          .group_by(_source_ip)
          .having(_msgs_where(_aligned == "pass") == 0)
          .count()
    )
    return {
        "days": days,
        "messages": messages,
        "compliant": compliant,
        "failing": messages - compliant,
        # None (et non 0) quand il n'y a aucun message : un taux de 0 % laisserait croire
        # que tout échoue, alors qu'il n'y a simplement rien à mesurer.
        "compliance_rate": round(100 * compliant / messages, 1) if messages else None,
        "dkim_pass": int(row.dkim_pass),
        "spf_pass": int(row.spf_pass),
        "quarantined": int(row.quarantined),
        "rejected": int(row.rejected),
        "sources": int(row.sources),
        "failing_sources": failing_sources,
    }


@router.get("/dmarc/timeseries")
def dmarc_timeseries(days: int = Query(30, ge=1, le=365), db=Depends(get_db)):
    """Volume quotidien, authentifié vs échoué."""
    rows = (db.query(
                ReportRow.report_date.label("day"),
                _msgs_where(_aligned == "pass").label("compliant"),
                _msgs_where(_aligned != "pass").label("failing"),
            )
            .filter(_since(days), _est_une_ligne_dmarc)
            .group_by(ReportRow.report_date)
            .order_by(ReportRow.report_date)
            .all())
    return [{"day": d.isoformat(), "compliant": int(c), "failing": int(f)}
            for d, c, f in rows]


@router.get("/dmarc/sources")
def dmarc_sources(days: int = Query(30, ge=1, le=365),
                  limit: int = Query(12, ge=1, le=100), db=Depends(get_db)):
    """Qui envoie du courrier au nom du domaine, et avec quel taux d'authentification.
    Trié par volume : une IP qui échoue sur 3 messages compte moins qu'une qui échoue
    sur 4 000."""
    rows = (db.query(
                _source_ip.label("ip"),
                func.coalesce(func.sum(_msgs), 0).label("messages"),
                _msgs_where(_aligned == "pass").label("compliant"),
                func.max(ReportRow.report_date).label("last_seen"),
                func.min(_reporter).label("reporter"),
            )
            .filter(_since(days), _est_une_ligne_dmarc)
            .group_by(_source_ip)
            .order_by(func.coalesce(func.sum(_msgs), 0).desc())
            .limit(limit)
            .all())
    return [{
        "source_ip": ip,
        "messages": int(m),
        "compliant": int(c),
        "failing": int(m) - int(c),
        "compliance_rate": round(100 * int(c) / int(m), 1) if int(m) else None,
        "last_seen": ls.isoformat() if ls else None,
        "reporter": rep,
    } for ip, m, c, ls, rep in rows]
