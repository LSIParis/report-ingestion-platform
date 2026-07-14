from fastapi import FastAPI

from app.api import admin, emails, ingest, ip_intel, metrics, mta_sts, reports
from app.auth.login import router as auth_router
from app.auth.middleware import TenantMiddleware

app = FastAPI(title="Report Ingestion Platform")
app.add_middleware(TenantMiddleware)

app.include_router(auth_router)
app.include_router(reports.router)
app.include_router(emails.router)
app.include_router(metrics.router)
app.include_router(admin.router)
app.include_router(ip_intel.router)
app.include_router(ingest.router)     # /ingest/ses — public, sécurisé par signature SNS
app.include_router(mta_sts.router)    # /.well-known/mta-sts.txt — public (lu par les MTA)


@app.get("/health")
def health():
    return {"status": "ok"}
