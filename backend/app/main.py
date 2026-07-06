from fastapi import FastAPI

from app.api import admin, emails, metrics, reports
from app.auth.login import router as auth_router
from app.auth.middleware import TenantMiddleware

app = FastAPI(title="Report Ingestion Platform")
app.add_middleware(TenantMiddleware)

app.include_router(auth_router)
app.include_router(reports.router)
app.include_router(emails.router)
app.include_router(metrics.router)
app.include_router(admin.router)


@app.get("/health")
def health():
    return {"status": "ok"}
