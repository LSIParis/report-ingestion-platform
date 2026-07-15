from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

# Les colonnes d'identité sont des UUID en base. Pydantic v2 ne coerce PAS un UUID vers
# `str` : un champ déclaré `id: str` lève une erreur de validation à la sérialisation.
# On les type donc en UUID — le rendu JSON reste une chaîne, le front ne voit aucune
# différence.


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email_id: UUID
    source_type: str
    status: str
    profile_id: str | None
    row_count: int
    parsed_at: datetime | None
    created_at: datetime
    kind: str
    reporter: str | None
    total_units: int | None
    failing_units: int | None
    units_partial: bool
    period_start: date | None
    period_end: date | None


class ReportRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    report_date: date | None
    data: dict


class ParsingErrorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    severity: str
    code: str
    message: str
    context: dict | None
    created_at: datetime


class EmailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID | None
    from_address: str
    subject: str
    status: str
    resolved_by: str | None
    received_at: datetime


class AssignTenantIn(BaseModel):
    tenant_id: str


class MetricsSummaryOut(BaseModel):
    total: int
    parsed_ok: int
    parsed_partial: int
    failed: int
    needs_review: int
