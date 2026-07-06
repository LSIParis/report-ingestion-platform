from datetime import date, datetime

from pydantic import BaseModel


class ReportOut(BaseModel):
    id: str
    email_id: str
    source_type: str
    status: str
    profile_id: str | None
    row_count: int
    parsed_at: datetime | None
    created_at: datetime

    class Config:
        from_attributes = True


class ReportRowOut(BaseModel):
    id: str
    report_date: date | None
    data: dict

    class Config:
        from_attributes = True


class ParsingErrorOut(BaseModel):
    id: str
    severity: str
    code: str
    message: str
    context: dict | None
    created_at: datetime

    class Config:
        from_attributes = True


class EmailOut(BaseModel):
    id: str
    tenant_id: str | None
    from_address: str
    subject: str
    status: str
    resolved_by: str | None
    received_at: datetime

    class Config:
        from_attributes = True


class AssignTenantIn(BaseModel):
    tenant_id: str


class MetricsSummaryOut(BaseModel):
    total: int
    parsed_ok: int
    parsed_partial: int
    failed: int
    needs_review: int
