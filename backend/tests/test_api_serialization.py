"""Sérialisation des réponses d'API.

Ces tests existent parce que TOUTES les routes paginées renvoyaient un 500 en
production : `Page.items` était typé `list[Any]`, donc Pydantic v2 recevait des objets
SQLAlchemy bruts (« Unable to serialize unknown type: Report »). Et les champs `id: str`
ne coercent pas un UUID sous Pydantic v2.

On valide les schémas contre des objets ORM réels — sans base ni réseau.
"""
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.pagination import Page
from app.api.schemas import EmailOut, ReportOut, ReportRowOut
from app.db.models import Email, Report, ReportRow


def _report() -> Report:
    return Report(id=uuid4(), tenant_id=uuid4(), email_id=uuid4(),
                  source_type="attachment", status="parsed_ok",
                  profile_id="_default_dmarc_xml", row_count=7,
                  parsed_at=datetime.now(timezone.utc),
                  created_at=datetime.now(timezone.utc))


def test_report_orm_est_serialisable():
    out = ReportOut.model_validate(_report())
    dumped = out.model_dump(mode="json")
    assert isinstance(dumped["id"], str)          # UUID -> chaîne en JSON
    assert dumped["row_count"] == 7


def test_page_generique_convertit_les_objets_orm():
    # C'est LE cas qui plantait : des objets ORM passés à une Page.
    page = Page[ReportOut].model_validate(
        {"items": [_report(), _report()], "total": 2, "page": 1, "size": 50})
    dumped = page.model_dump(mode="json")
    assert len(dumped["items"]) == 2
    assert dumped["items"][0]["status"] == "parsed_ok"


def test_page_non_parametree_refuse_un_objet_orm():
    # Garde-fou : si quelqu'un remet un Page nu sur une route, ça doit casser au test,
    # pas en production.
    with pytest.raises(ValidationError):
        Page.model_validate({"items": [_report()], "total": 1, "page": 1, "size": 50})


def test_email_et_row_serialisables():
    em = Email(id=uuid4(), tenant_id=uuid4(), message_id="m@x",
               from_address="noreply-dmarc-support@google.com",
               subject="Report domain: exemple.com", status="parsed_ok",
               resolved_by="subject_regex", received_at=datetime.now(timezone.utc),
               raw_object_key="raw/x.eml")
    assert EmailOut.model_validate(em).model_dump(mode="json")["resolved_by"] == "subject_regex"

    row = ReportRow(id=uuid4(), tenant_id=uuid4(), report_id=uuid4(),
                    report_date=date(2026, 7, 6),
                    data={"source_ip": "209.85.220.41", "aligned": "pass"})
    dumped = ReportRowOut.model_validate(row).model_dump(mode="json")
    assert dumped["report_date"] == "2026-07-06"
    assert dumped["data"]["aligned"] == "pass"
