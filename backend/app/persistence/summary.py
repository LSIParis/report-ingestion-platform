"""Le resume d'un rapport, calcule a partir de ses lignes deja normalisees.

Fonction PURE (aucun acces DB) : c'est la seule regle metier du lot, isolee pour etre
evidente et testable, et pour servir de source UNIQUE a l'ingestion (persist) comme au
backfill (migration 0010). Deux disciplines la traversent :

 - "null != 0" : un compteur illisible n'est JAMAIS compte comme zero (int_or_none), sinon
   on afficherait "0 % d'echec" sur un rapport dont on ne sait pas lire les compteurs --
   rassurant et faux. `units_partial` signale un total qui n'est qu'un minorant.
 - TLS ne compte QUE les lignes `summary` : les lignes `failure` re-decrivent les memes
   sessions echouees par type (les additionner double-compterait). Voir tls_posture.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from app.services.counters import int_or_none

_TLS_KINDS = frozenset({"summary", "failure"})


@dataclass
class ReportSummary:
    kind: str                      # 'dmarc' | 'tls'
    reporter: str | None
    total_units: int | None        # messages (DMARC) ou sessions (TLS) ; None = illisible
    failing_units: int | None      # part en echec ; None = illisible
    units_partial: bool            # total/failing sont des MINORANTS (>= 1 compteur illisible)
    period_start: date | None
    period_end: date | None


def summarize(rows: list[dict]) -> ReportSummary:
    kind = _infer_kind(rows)
    reporter, period_start, period_end = _header(rows)
    if kind == "tls":
        total, failing, partial = _count_tls(rows)
    else:
        total, failing, partial = _count_dmarc(rows)
    return ReportSummary(kind=kind, reporter=reporter, total_units=total,
                         failing_units=failing, units_partial=partial,
                         period_start=period_start, period_end=period_end)


def _infer_kind(rows: list[dict]) -> str:
    # Seules les lignes TLS portent une cle `kind`. Absente => DMARC (y compris rapport vide).
    for d in rows:
        if d.get("kind") in _TLS_KINDS:
            return "tls"
    return "dmarc"


def _header(rows: list[dict]) -> tuple[str | None, date | None, date | None]:
    # L'en-tete (emetteur, periode) est identique sur toutes les lignes : on prend la
    # premiere valeur non nulle, sans supposer quelle ligne la porte.
    reporter = period_start = period_end = None
    for d in rows:
        if reporter is None and d.get("reporter"):
            reporter = str(d["reporter"])
        if period_start is None:
            period_start = _as_date(d.get("report_date"))
        if period_end is None:
            period_end = _as_date(d.get("period_end"))
    return reporter, period_start, period_end


def _as_date(value) -> date | None:
    # Ingestion : objets Python (date). Backfill : chaines ISO issues du JSONB. On accepte
    # les deux ; tout le reste (None, illisible) -> None.
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _count_dmarc(rows: list[dict]) -> tuple[int | None, int | None, bool]:
    total = failing = 0
    has_known = has_unknown = False
    for d in rows:
        n = int_or_none(d.get("message_count"))
        if n is None:
            has_unknown = True
            continue
        has_known = True
        total += n
        if d.get("aligned") != "pass":
            failing += n
    if not has_known:
        # Aucune ligne chiffrable : total inconnu (None) s'il y avait des lignes illisibles,
        # vrai zero s'il n'y avait aucune ligne.
        return (None, None, False) if has_unknown else (0, 0, False)
    return total, failing, has_unknown


def _count_tls(rows: list[dict]) -> tuple[int | None, int | None, bool]:
    # UNIQUEMENT les lignes `summary` (voir docstring de module). Chaque moitie lisible est
    # comptee separement (comme tls_posture) : jeter la ligne entiere ferait disparaitre un
    # echec REEL et CONNU quand seul `successful_sessions` manque.
    total_ok = total_failed = 0
    has_known = has_unknown = False
    for d in rows:
        if d.get("kind") != "summary":
            continue
        ok = int_or_none(d.get("successful_sessions"))
        failed = int_or_none(d.get("failed_sessions"))
        if ok is None or failed is None:
            has_unknown = True
        if ok is not None:
            total_ok += ok
            has_known = True
        if failed is not None:
            total_failed += failed
            has_known = True
    if not has_known:
        return (None, None, False) if has_unknown else (0, 0, False)
    return total_ok + total_failed, total_failed, has_unknown
