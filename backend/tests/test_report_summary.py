"""summarize() : la seule regle metier du lot, isolee et pure.

On y verifie surtout la discipline "null != 0" (un compteur illisible n'est jamais
lu comme zero) et que TLS ne compte QUE les lignes `summary` (les lignes `failure`
sont un detail par type des memes sessions).
"""
from datetime import date

from app.persistence.summary import summarize


def _dmarc_row(count, aligned, reporter="google.com",
               report_date="2026-07-01", period_end="2026-07-01"):
    return {"message_count": count, "aligned": aligned, "reporter": reporter,
            "report_date": report_date, "period_end": period_end}


def test_dmarc_total_et_echec():
    rows = [_dmarc_row(4000, "pass"), _dmarc_row(500, "fail"), _dmarc_row(10, "fail")]
    s = summarize(rows)
    assert s.kind == "dmarc"
    assert s.reporter == "google.com"
    assert s.total_units == 4510
    assert s.failing_units == 510
    assert s.units_partial is False
    assert s.period_start == date(2026, 7, 1)
    assert s.period_end == date(2026, 7, 1)


def test_dmarc_compteur_illisible_rend_minorant():
    # Une ligne avec message_count None : le total est un MINORANT, jamais gonfle de 0.
    rows = [_dmarc_row(100, "pass"), _dmarc_row(None, "fail")]
    s = summarize(rows)
    assert s.total_units == 100
    assert s.failing_units == 0
    assert s.units_partial is True


def test_dmarc_tout_illisible_rend_none():
    rows = [_dmarc_row(None, "pass"), _dmarc_row(None, "fail")]
    s = summarize(rows)
    assert s.total_units is None
    assert s.failing_units is None
    assert s.units_partial is False


def _tls_summary(ok, failed, reporter="microsoft.com",
                 report_date="2026-07-02", period_end="2026-07-02"):
    return {"kind": "summary", "successful_sessions": ok, "failed_sessions": failed,
            "reporter": reporter, "report_date": report_date, "period_end": period_end}


def _tls_failure(sessions, result_type="certificate-expired"):
    return {"kind": "failure", "failure_sessions": sessions, "result_type": result_type,
            "reporter": "microsoft.com"}


def test_tls_compte_seulement_les_lignes_summary():
    # Les lignes `failure` re-decrivent les MEMES sessions echouees : ne pas les additionner.
    rows = [_tls_summary(90, 10), _tls_failure(10)]
    s = summarize(rows)
    assert s.kind == "tls"
    assert s.reporter == "microsoft.com"
    assert s.total_units == 100          # 90 + 10, uniquement depuis summary
    assert s.failing_units == 10
    assert s.units_partial is False


def test_tls_failed_sessions_illisible_rend_minorant():
    rows = [_tls_summary(90, None)]
    s = summarize(rows)
    assert s.total_units == 90           # moitie lisible conservee
    assert s.failing_units == 0
    assert s.units_partial is True


def test_kind_deduit_du_contenu_sans_ligne_tls():
    # Aucune ligne `kind` => dmarc.
    assert summarize([_dmarc_row(1, "pass")]).kind == "dmarc"


def test_rapport_vide_defaut_dmarc_zero():
    s = summarize([])
    assert s.kind == "dmarc"
    assert s.total_units == 0
    assert s.failing_units == 0
    assert s.reporter is None
    assert s.period_start is None
