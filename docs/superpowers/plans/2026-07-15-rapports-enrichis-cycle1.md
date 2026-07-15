# Rapports enrichis — Cycle 1 — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dénormaliser un résumé (type, émetteur, volume, taux d'échec, période) sur chaque rapport, et l'exploiter pour enrichir la liste des rapports (colonnes + onglets DMARC/TLS/Tous).

**Architecture:** Une fonction pure `summarize(rows)` calcule le résumé à partir des lignes déjà normalisées ; `PersistenceService.persist()` l'appelle à l'ingestion, et la migration `0010` l'appelle pour backfiller les rapports existants (une seule source de vérité). L'API expose ces colonnes et un filtre `kind`. Le frontend ajoute des onglets et trois colonnes.

**Tech Stack:** Python 3.12 · SQLAlchemy 2.0 · Alembic · FastAPI · pytest · React 19 + TypeScript · Vite · Tailwind.

**Spec:** `docs/superpowers/specs/2026-07-15-rapports-enrichis-cycle1-design.md`

## Global Constraints

- **Isolation multitenant (CLAUDE.md)** : aucune route ne pose de `WHERE tenant_id` applicatif — la session est déjà scopée par RLS. Le test d'isolation cross-tenant (`tests/test_tenant_isolation.py`) reste vert et **bloque le merge**.
- **Discipline « null ≠ 0 »** : un compteur illisible (`None` ou non entier) n'est **jamais** lu comme `0`. Réutiliser `app.services.counters.int_or_none`. `total_units`/`failing_units` valent `None` quand la magnitude est inconnue ; `units_partial=True` signale un minorant.
- **Définition « en échec »** : DMARC = `message_count` des lignes où `aligned != "pass"` (identique à `app/api/metrics.py`) ; TLS = `failed_sessions` des lignes `kind == "summary"` uniquement (identique à `app/services/tls_posture.py` — les lignes `kind == "failure"` sont un détail par type des MÊMES sessions, les additionner double-compterait).
- **Une seule source de vérité** : la logique de résumé vit dans `summarize()`. L'ingestion **et** le backfill l'appellent. Aucune ré-implémentation en SQL.
- **`kind` déduit du contenu** : seules les lignes TLS portent une clé `kind` (`"summary"`/`"failure"`). Une ligne DMARC n'en a pas. `summarize()` déduit donc `tls` si au moins une ligne a `kind ∈ {summary, failure}`, sinon `dmarc`. Un rapport sans ligne (rapport en échec) → `dmarc` par défaut (valeur sûre, non nulle).
- **Clés canoniques** (après normalisation, cf. `backend/profiles/_default_*.json`) : `reporter`, `message_count`, `aligned` (`"pass"`/`"fail"`), `report_date` (= début de période), `period_end`, et côté TLS `kind`, `successful_sessions`, `failed_sessions`.
- **Commentaires et libellés en français. Messages de commit en français SANS accents.**
- **Back-end** : tests via `docker compose exec api pytest …` (un vrai PostgreSQL est requis pour les tests d'intégration). Lint : `docker compose exec api ruff check app`.
- **Front-end** : pas de harnais de test. Vérification = `tsc -b` + `vite build` verts, via :
  ```bash
  MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/frontend:/app" -w /app frontend sh -c "npm install --silent && npx tsc -b && npx vite build"
  ```
  (depuis `infra/`, Git Bash — `MSYS_NO_PATHCONV=1` INDISPENSABLE).

## Structure des fichiers

| Fichier | Rôle |
|---|---|
| `backend/app/persistence/summary.py` | **Créer.** `ReportSummary` + `summarize(rows)` (pure). |
| `backend/tests/test_report_summary.py` | **Créer.** Tests unitaires de `summarize()`. |
| `backend/app/db/models.py` | **Modifier.** 7 colonnes sur `Report`. |
| `backend/migrations/versions/0010_report_summary.py` | **Créer.** Colonnes + backfill via `summarize()`. |
| `backend/app/persistence/service.py` | **Modifier.** `persist()` appelle `summarize()`. |
| `backend/tests/test_persistence_summary.py` | **Créer.** Intégration : `persist()` remplit les colonnes. |
| `backend/app/api/schemas.py` | **Modifier.** `ReportOut` gagne les 7 champs. |
| `backend/app/api/reports.py` | **Modifier.** Filtre `?kind=`. |
| `backend/tests/test_reports_kind_filter.py` | **Créer.** Test du filtre `kind`. |
| `frontend/src/api/reports.ts` | **Modifier.** `Report` gagne les champs ; `useReports` passe `kind`. |
| `frontend/src/pages/ReportsList.tsx` | **Modifier.** Onglets + colonnes. |

Ordre : 1 (`summarize`) → 2 (colonnes + migration) → 3 (ingestion) → 4 (API) → 5 (frontend). Chaque tâche produit un livrable testable indépendamment.

---

### Task 1: Fonction pure `summarize(rows)`

**Files:**
- Create: `backend/app/persistence/summary.py`
- Test: `backend/tests/test_report_summary.py`

**Interfaces:**
- Consumes: `app.services.counters.int_or_none` (existant : `int_or_none(value) -> int | None`, `None`/non castable → `None`, jamais `0`).
- Produces:
  - `@dataclass ReportSummary` avec les champs : `kind: str`, `reporter: str | None`, `total_units: int | None`, `failing_units: int | None`, `units_partial: bool`, `period_start: date | None`, `period_end: date | None`.
  - `summarize(rows: list[dict]) -> ReportSummary`.

- [ ] **Step 1: Écrire les tests d'abord**

Créer `backend/tests/test_report_summary.py` :

```python
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
```

- [ ] **Step 2: Lancer les tests, vérifier qu'ils échouent**

Run: `docker compose exec api pytest tests/test_report_summary.py -q`
Expected: FAIL (`ModuleNotFoundError: app.persistence.summary`).

- [ ] **Step 3: Écrire `summarize()`**

Créer `backend/app/persistence/summary.py` :

```python
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
```

- [ ] **Step 4: Lancer les tests, vérifier qu'ils passent**

Run: `docker compose exec api pytest tests/test_report_summary.py -q`
Expected: PASS (9 tests).

- [ ] **Step 5: Lint + commit**

```bash
docker compose exec api ruff check app/persistence/summary.py
git add backend/app/persistence/summary.py backend/tests/test_report_summary.py
git commit -m "feat(reports): fonction pure summarize() du resume d un rapport

Type (deduit du contenu), emetteur, periode, volume et part en echec, avec la discipline
null != 0 (compteur illisible jamais lu comme zero, minorant signale par units_partial).
TLS ne compte que les lignes summary. Source unique pour l ingestion et le backfill."
```

---

### Task 2: Colonnes `report` + migration `0010` (schéma + backfill)

**Files:**
- Modify: `backend/app/db/models.py` (classe `Report`, ~lignes 95-106 ; imports en tête)
- Create: `backend/migrations/versions/0010_report_summary.py`

**Interfaces:**
- Consumes: `summarize(rows)` (Task 1).
- Produces: sept colonnes sur `Report` : `kind` (NOT NULL), `reporter`, `total_units`, `failing_units`, `units_partial` (NOT NULL, défaut `false`), `period_start`, `period_end`.

- [ ] **Step 1: Ajouter les imports nécessaires dans `models.py`**

En tête de `backend/app/db/models.py`, l'import SQLAlchemy existant liste les types utilisés. Vérifier qu'il contient `Boolean`, `Date` et `text` ; sinon les ajouter. Exemple d'import complété (adapter à la ligne réelle) :

```python
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Text, func, text
```

(La ligne d'import actuelle importe déjà `DateTime, ForeignKey, Integer, Text, func` ; ajouter les manquants `Boolean, Date, text`.)

- [ ] **Step 2: Ajouter les colonnes à la classe `Report`**

Dans `backend/app/db/models.py`, classe `Report`, juste après `created_at` (fin de la classe, avant la ligne blanche qui précède `class ReportRow`), ajouter :

```python
    # Resume denormalise (voir app/persistence/summary.py) : rempli a l'ingestion et
    # backfille par la migration 0010. Evite d'agreger les lignes a chaque affichage de
    # liste et rend le filtre par type trivial.
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    reporter: Mapped[str | None] = mapped_column(Text)
    total_units: Mapped[int | None] = mapped_column(Integer)
    failing_units: Mapped[int | None] = mapped_column(Integer)
    units_partial: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
```

`date` est déjà importé dans `models.py` (utilisé par `ReportRow.report_date`). Sinon, ajouter `from datetime import date` au groupe d'imports `datetime`.

- [ ] **Step 3: Écrire la migration `0010`**

Créer `backend/migrations/versions/0010_report_summary.py` :

```python
"""report summary denormalise (cycle 1) : colonnes + backfill via summarize()

Revision ID: 0010_report_summary
Revises: 0009_alert_external_ref
"""
import sqlalchemy as sa
from alembic import op

revision = "0010_report_summary"
down_revision = "0009_alert_external_ref"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Colonnes. `kind` d'abord nullable : on ne peut la remplir qu'apres le backfill.
    op.add_column("report", sa.Column("kind", sa.Text(), nullable=True))
    op.add_column("report", sa.Column("reporter", sa.Text(), nullable=True))
    op.add_column("report", sa.Column("total_units", sa.Integer(), nullable=True))
    op.add_column("report", sa.Column("failing_units", sa.Integer(), nullable=True))
    op.add_column("report", sa.Column("units_partial", sa.Boolean(),
                                      nullable=False, server_default=sa.text("false")))
    op.add_column("report", sa.Column("period_start", sa.Date(), nullable=True))
    op.add_column("report", sa.Column("period_end", sa.Date(), nullable=True))

    # 2) Backfill : recalcule chaque rapport avec la MEME fonction que l'ingestion.
    # Import differe (dans upgrade) : evite tout effet de bord a l'import du module de migration.
    from app.persistence.summary import summarize

    bind = op.get_bind()
    report_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM report"))]
    upd = sa.text(
        "UPDATE report SET kind=:kind, reporter=:reporter, total_units=:total, "
        "failing_units=:failing, units_partial=:partial, period_start=:pstart, "
        "period_end=:pend WHERE id=:rid"
    )
    for rid in report_ids:
        # psycopg rend le JSONB `data` deja sous forme de dict.
        data_rows = [r[0] for r in bind.execute(
            sa.text("SELECT data FROM report_row WHERE report_id = :rid"), {"rid": rid})]
        s = summarize(data_rows)
        bind.execute(upd, {"kind": s.kind, "reporter": s.reporter, "total": s.total_units,
                           "failing": s.failing_units, "partial": s.units_partial,
                           "pstart": s.period_start, "pend": s.period_end, "rid": rid})

    # 3) Tous les rapports ont desormais un kind : on peut le rendre NOT NULL.
    op.alter_column("report", "kind", nullable=False)


def downgrade() -> None:
    for col in ("period_end", "period_start", "units_partial", "failing_units",
                "total_units", "reporter", "kind"):
        op.drop_column("report", col)
```

- [ ] **Step 4: Appliquer la migration et vérifier**

```bash
docker compose exec api alembic upgrade head
```
Expected : la migration `0010_report_summary` s'applique sans erreur.

Vérifier que les colonnes existent et que le backfill a tourné (les rapports de démo, s'il y en a, ont un `kind`) :
```bash
docker compose exec postgres psql -U postgres -d reports -c "SELECT kind, count(*) FROM report GROUP BY kind;"
```
Expected : une ou plusieurs lignes, chacune avec un `kind` non nul (`dmarc`/`tls`). Aucune ligne `kind IS NULL`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/db/models.py backend/migrations/versions/0010_report_summary.py
git commit -m "feat(reports): colonnes de resume sur report + migration 0010 avec backfill

Sept colonnes denormalisees (kind, reporter, total/failing_units, units_partial, periode).
Le backfill des rapports existants reutilise summarize() -- une seule source de verite,
applique automatiquement au deploiement (service migrate). kind NOT NULL apres remplissage."
```

---

### Task 3: `persist()` remplit le résumé à l'ingestion

**Files:**
- Modify: `backend/app/persistence/service.py`
- Test: `backend/tests/test_persistence_summary.py`

**Interfaces:**
- Consumes: `summarize(rows)` (Task 1) ; colonnes `Report` (Task 2) ; `ParseResult(status, rows, errors, metadata)` (`app/parsing/base.py`).
- Produces: un `Report` persisté dont les 7 colonnes de résumé sont remplies.

- [ ] **Step 1: Écrire le test d'intégration d'abord**

Créer `backend/tests/test_persistence_summary.py` :

```python
"""persist() pose le resume (summarize) sur le Report, DMARC comme TLS."""
import uuid
from datetime import date, datetime, timezone

from app.db.models import Email, Report, Tenant
from app.db.session import get_session
from app.parsing.base import ParseResult
from app.persistence.service import PersistenceService


def _tenant_email():
    with get_session() as db:
        t = Tenant(domain=f"persist-{uuid.uuid4().hex[:8]}.test", name="Persist")
        db.add(t)
        db.flush()
        em = Email(tenant_id=t.id, message_id=f"m-{uuid.uuid4()}",
                   from_address="x@y.test", subject="s",
                   received_at=datetime.now(timezone.utc), raw_object_key="raw/x.eml",
                   status="parsed_ok")
        db.add(em)
        db.flush()
        db.commit()
        return str(t.id), str(em.id)


def _cleanup(tid, eid):
    with get_session() as db:
        db.query(Report).filter_by(email_id=eid).delete()
        db.query(Email).filter_by(id=eid).delete()
        db.query(Tenant).filter_by(id=tid).delete()
        db.commit()


def test_persist_dmarc_remplit_le_resume():
    tid, eid = _tenant_email()
    try:
        rows = [
            {"message_count": 100, "aligned": "pass", "reporter": "google.com",
             "report_date": "2026-07-01", "period_end": "2026-07-01"},
            {"message_count": 5, "aligned": "fail", "reporter": "google.com",
             "report_date": "2026-07-01", "period_end": "2026-07-01"},
        ]
        result = ParseResult(status="ok", rows=rows)
        rid = PersistenceService().persist(
            tenant_id=tid, email_id=eid, attachment_id=None, profile_id="_default_dmarc_xml",
            source_type="body", result=result)
        with get_session() as db:
            r = db.get(Report, rid)
            assert r.kind == "dmarc"
            assert r.reporter == "google.com"
            assert r.total_units == 105
            assert r.failing_units == 5
            assert r.units_partial is False
            assert r.period_start == date(2026, 7, 1)
    finally:
        _cleanup(tid, eid)


def test_persist_tls_compte_les_sessions_summary():
    tid, eid = _tenant_email()
    try:
        rows = [
            {"kind": "summary", "successful_sessions": 90, "failed_sessions": 10,
             "reporter": "microsoft.com", "report_date": "2026-07-02",
             "period_end": "2026-07-02"},
            {"kind": "failure", "failure_sessions": 10, "result_type": "certificate-expired",
             "reporter": "microsoft.com"},
        ]
        result = ParseResult(status="ok", rows=rows)
        rid = PersistenceService().persist(
            tenant_id=tid, email_id=eid, attachment_id=None, profile_id="_default_tlsrpt_json",
            source_type="body", result=result)
        with get_session() as db:
            r = db.get(Report, rid)
            assert r.kind == "tls"
            assert r.total_units == 100
            assert r.failing_units == 10
    finally:
        _cleanup(tid, eid)
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `docker compose exec api pytest tests/test_persistence_summary.py -q`
Expected: FAIL (les colonnes de résumé sont `None`/défaut car `persist()` ne les remplit pas encore).

- [ ] **Step 3: Câbler `summarize()` dans `persist()`**

Dans `backend/app/persistence/service.py`, ajouter l'import en tête (avec les autres imports `app.`) :

```python
from app.persistence.summary import summarize
```

Puis, dans `persist()`, juste avant la construction de `report = Report(...)`, calculer le résumé, et l'injecter dans le constructeur :

```python
            resume = summarize(result.rows)
            report = Report(
                tenant_id=tenant_id, email_id=email_id, attachment_id=attachment_id,
                profile_id=profile_id, source_type=source_type,
                status=result.status, row_count=len(result.rows),
                parsed_at=datetime.now(timezone.utc),
                kind=resume.kind, reporter=resume.reporter,
                total_units=resume.total_units, failing_units=resume.failing_units,
                units_partial=resume.units_partial,
                period_start=resume.period_start, period_end=resume.period_end,
            )
```

- [ ] **Step 4: Lancer, vérifier que ça passe**

Run: `docker compose exec api pytest tests/test_persistence_summary.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Lint + commit**

```bash
docker compose exec api ruff check app/persistence/service.py
git add backend/app/persistence/service.py backend/tests/test_persistence_summary.py
git commit -m "feat(reports): persist() remplit le resume du rapport a l ingestion

Un retraitement repasse par persist(), donc le resume est recalcule sans traitement
particulier."
```

---

### Task 4: API — champs `ReportOut` + filtre `?kind=`

**Files:**
- Modify: `backend/app/api/schemas.py` (classe `ReportOut`, ~lignes 12-22)
- Modify: `backend/app/api/reports.py` (`list_reports`, ~lignes 16-26)
- Test: `backend/tests/test_reports_kind_filter.py`

**Interfaces:**
- Consumes: colonnes `Report` (Task 2).
- Produces: `GET /reports?kind=dmarc|tls` filtré ; `ReportOut` exposant `kind`, `reporter`, `total_units`, `failing_units`, `units_partial`, `period_start`, `period_end`.

- [ ] **Step 1: Écrire le test d'abord**

Créer `backend/tests/test_reports_kind_filter.py` :

```python
"""GET /reports?kind= filtre par type, sous RLS (aucun WHERE tenant_id applicatif)."""
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.reports import router
from app.auth.middleware import TenantContext
from app.db.models import Email, Report, Tenant
from app.db.session import get_session


def _client(tenant_id):
    # Meme montage que test_metrics_dmarc.py / test_ip_intel_api.py : un VRAI contexte
    # tenant injecte dans request.state.tenant (RLS active, bypass=False). get_db lit ce
    # contexte et pose SET LOCAL app.current_tenant. NE PAS court-circuiter get_db par une
    # get_session() (plan worker BYPASSRLS) : le test verrait alors toute la base.
    app = FastAPI()
    ctx = TenantContext(user="kind-test@example.test", role="tenant_viewer",
                        tenant_ids=(tenant_id,), active_tenant=tenant_id, bypass=False)

    @app.middleware("http")
    async def inject_ctx(request, call_next):
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def test_filtre_kind():
    with get_session() as db:
        t = Tenant(domain=f"kind-{uuid.uuid4().hex[:8]}.test", name="Kind")
        db.add(t)
        db.flush()
        em = Email(tenant_id=t.id, message_id=f"m-{uuid.uuid4()}", from_address="x@y.test",
                   subject="s", received_at=datetime.now(timezone.utc),
                   raw_object_key="raw/x.eml", status="parsed_ok")
        db.add(em)
        db.flush()
        db.add(Report(tenant_id=t.id, email_id=em.id, source_type="body", status="ok",
                      kind="dmarc", reporter="google.com", total_units=10, failing_units=1,
                      units_partial=False))
        db.add(Report(tenant_id=t.id, email_id=em.id, source_type="body", status="ok",
                      kind="tls", reporter="microsoft.com", total_units=5, failing_units=0,
                      units_partial=False))
        db.commit()
        tid = str(t.id)

    try:
        c = _client(tid)
        tous = c.get("/reports").json()
        assert tous["total"] == 2
        dmarc = c.get("/reports?kind=dmarc").json()
        assert dmarc["total"] == 1
        assert dmarc["items"][0]["kind"] == "dmarc"
        assert dmarc["items"][0]["reporter"] == "google.com"
        assert dmarc["items"][0]["total_units"] == 10
        tls = c.get("/reports?kind=tls").json()
        assert tls["total"] == 1
        assert tls["items"][0]["kind"] == "tls"
    finally:
        with get_session() as db:
            db.query(Report).filter_by(tenant_id=tid).delete()
            db.query(Email).filter_by(tenant_id=tid).delete()
            db.query(Tenant).filter_by(id=tid).delete()
            db.commit()
```

> Ce montage (contexte injecté dans `request.state.tenant`, `bypass=False`) est exactement celui de `backend/tests/test_metrics_dmarc.py:148-158` et `test_ip_intel_api.py`. Le point vérifié : deux rapports (un `dmarc`, un `tls`) pour un même tenant, `?kind=` filtre, et les nouveaux champs (`kind`, `reporter`, `total_units`) sont bien sérialisés dans la réponse.

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `docker compose exec api pytest tests/test_reports_kind_filter.py -q`
Expected: FAIL (paramètre `kind` inconnu / champs absents de `ReportOut`).

- [ ] **Step 3: Ajouter les champs à `ReportOut`**

Dans `backend/app/api/schemas.py`, classe `ReportOut`, ajouter après `created_at` :

```python
    kind: str
    reporter: str | None
    total_units: int | None
    failing_units: int | None
    units_partial: bool
    period_start: date | None
    period_end: date | None
```

Ajouter l'import de `date` en tête si absent (le fichier importe déjà `datetime`) :
```python
from datetime import date, datetime
```

- [ ] **Step 4: Ajouter le filtre `kind` à `list_reports`**

Dans `backend/app/api/reports.py`, remplacer la signature et le corps de `list_reports` par :

```python
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
```

- [ ] **Step 5: Lancer les tests (nouveau + isolation)**

```bash
docker compose exec api pytest tests/test_reports_kind_filter.py tests/test_tenant_isolation.py -q
```
Expected: PASS. Le test d'isolation cross-tenant reste vert (les nouvelles colonnes n'ouvrent aucune fuite : tout passe par la session scopée).

- [ ] **Step 6: Lint + commit**

```bash
docker compose exec api ruff check app/api/reports.py app/api/schemas.py
git add backend/app/api/reports.py backend/app/api/schemas.py backend/tests/test_reports_kind_filter.py
git commit -m "feat(reports): ReportOut expose le resume + filtre GET /reports?kind=

Filtre par type = simple WHERE report.kind, grace a la denormalisation. Isolation RLS
inchangee (aucun WHERE tenant_id applicatif)."
```

---

### Task 5: Frontend — onglets + colonnes

**Files:**
- Modify: `frontend/src/api/reports.ts` (interface `Report` ~lignes 5-14 ; `useReports` ~lignes 46-56)
- Modify: `frontend/src/pages/ReportsList.tsx` (composant complet)

**Interfaces:**
- Consumes: `GET /reports?kind=` et les champs de `ReportOut` (Task 4).
- Produces: rien (feuille).

- [ ] **Step 1: Étendre l'interface `Report` et `useReports`**

Dans `frontend/src/api/reports.ts`, ajouter à l'interface `Report` (après `created_at`) :

```ts
  kind: "dmarc" | "tls";
  reporter: string | null;
  total_units: number | null;
  failing_units: number | null;
  units_partial: boolean;
  period_start: string | null;
  period_end: string | null;
```

Puis, dans `useReports`, ajouter le paramètre `kind` (signature + query string). Remplacer la fonction par :

```ts
export function useReports(filters: { status?: string; brand?: string; kind?: string; page: number }) {
  const qs = new URLSearchParams();
  if (filters.status) qs.set("status_f", filters.status);
  if (filters.brand) qs.set("brand", filters.brand);
  if (filters.kind) qs.set("kind", filters.kind);
  qs.set("page", String(filters.page));
  return useQuery({
    queryKey: ["reports", filters],
    queryFn: () => api<Page<Report>>(`/reports?${qs}`),
    placeholderData: (prev) => prev,
  });
}
```

- [ ] **Step 2: Réécrire `ReportsList.tsx` (onglets + colonnes)**

Remplacer tout le contenu de `frontend/src/pages/ReportsList.tsx` par :

```tsx
import { Link, useSearchParams } from "react-router-dom";

import { type Report, useReports } from "../api/reports";
import { StatusBadge } from "../components/StatusBadge";

const ONGLETS = [
  { key: "", label: "Tous" },
  { key: "dmarc", label: "DMARC" },
  { key: "tls", label: "TLS" },
] as const;

export function ReportsList() {
  const [sp, setSp] = useSearchParams();
  const status = sp.get("status") ?? "";
  const brand = sp.get("brand") ?? "";
  const kind = sp.get("kind") ?? "";
  const page = Number(sp.get("page") ?? 1);

  const set = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    v ? n.set(k, v) : n.delete(k);
    n.set("page", "1");
    setSp(n);
  };

  const { data, isLoading } = useReports({ status, brand, kind, page });

  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold mb-4">Rapports reçus</h1>

      {/* Onglets par type : pilotent ?kind=. « Tous » = pas de parametre. */}
      <div className="flex gap-1 mb-4 border-b">
        {ONGLETS.map((o) => (
          <button
            key={o.key}
            onClick={() => set("kind", o.key)}
            className={`px-3 py-1.5 text-sm -mb-px border-b-2 ${
              kind === o.key
                ? "border-blue-600 text-blue-600 font-medium"
                : "border-transparent text-gray-500 hover:text-gray-800"
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>

      <div className="flex gap-3 mb-4">
        <select value={status} onChange={(e) => set("status", e.target.value)}
                className="border rounded px-2 py-1">
          <option value="">Tous statuts</option>
          <option value="ok">OK</option>
          <option value="partial">Partiel</option>
          <option value="failed">Échec</option>
        </select>
        <input placeholder="Marque / expéditeur" defaultValue={brand}
               onBlur={(e) => set("brand", e.target.value)}
               className="border rounded px-2 py-1" />
      </div>

      {isLoading ? (
        <p>Chargement…</p>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-gray-500 border-b">
            <tr>
              <th className="py-2">Reçu</th>
              <th>Type</th>
              <th>Organisation émettrice</th>
              <th>Source</th>
              <th>Lignes</th>
              <th>Volume · échec</th>
              <th>Statut</th>
            </tr>
          </thead>
          <tbody>
            {data!.items.map((r) => (
              <tr key={r.id} className="border-b hover:bg-gray-50">
                <td className="py-2">
                  <Link to={`/reports/${r.id}`} className="text-blue-600">
                    {new Date(r.created_at).toLocaleString()}
                  </Link>
                </td>
                <td><TypeBadge kind={r.kind} /></td>
                <td>{r.reporter ?? "—"}</td>
                <td>{r.source_type}</td>
                <td>{r.row_count}</td>
                <td>
                  <span className="tabular-nums">{fmtVolume(r)}</span>
                  <span className="ml-2 text-gray-500">{fmtRate(r)}</span>
                </td>
                <td>
                  <StatusBadge status={r.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="flex gap-2 mt-4 items-center">
        <button disabled={page <= 1} onClick={() => set("page", String(page - 1))}
                className="disabled:opacity-40">←</button>
        <span className="text-sm">Page {page} · {data?.total ?? 0} rapports</span>
        <button disabled={(data?.items.length ?? 0) < 50} onClick={() => set("page", String(page + 1))}
                className="disabled:opacity-40">→</button>
      </div>
    </div>
  );
}

function TypeBadge({ kind }: { kind: Report["kind"] }) {
  const tls = kind === "tls";
  return (
    <span className={`rounded px-1.5 py-0.5 text-xs ${
      tls ? "bg-purple-100 text-purple-800" : "bg-blue-100 text-blue-800"
    }`}>
      {tls ? "TLS" : "DMARC"}
    </span>
  );
}

/* Volume : « — » si le total est illisible (null), jamais « 0 ». « au moins N » si le total
   n'est qu'un minorant (un compteur illisible). Meme convention que IpPanel/MtaStsPanel. */
function fmtVolume(r: Report): string {
  if (r.total_units === null) return "—";
  const n = r.total_units.toLocaleString("fr-FR");
  return r.units_partial ? `au moins ${n}` : n;
}

/* Taux d'echec : « — » si le total est inconnu ou nul (pas de « 0 % » rassurant et faux). */
function fmtRate(r: Report): string {
  if (r.total_units === null || r.total_units === 0) return "—";
  const pct = Math.round((100 * (r.failing_units ?? 0)) / r.total_units);
  return `${pct} % en échec`;
}
```

- [ ] **Step 3: Lancer la vérification frontend**

Depuis `infra/` (Git Bash) :
```bash
MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/frontend:/app" -w /app frontend sh -c "npm install --silent && npx tsc -b && npx vite build"
```
Expected : `tsc` sans erreur (le champ `kind`/`units_partial` etc. sont typés), `vite build` réussi.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/reports.ts frontend/src/pages/ReportsList.tsx
git commit -m "feat(front): liste des rapports enrichie (onglets DMARC/TLS + colonnes)

Onglets par type (?kind=), colonnes Type, Organisation emettrice (Source conservee),
Volume + taux d echec avec la convention null/minorant des panneaux existants."
```

---

## Vérification finale

- [ ] `docker compose exec api pytest` — toute la suite verte (dont `test_report_summary`, `test_persistence_summary`, `test_reports_kind_filter`, et **`test_tenant_isolation` bloquant**).
- [ ] `docker compose exec api ruff check app` — propre.
- [ ] Vérification frontend (voir Global Constraints) — `tsc -b` + `vite build` verts.
- [ ] **Contrôle réel navigateur** (le build ne prouve pas le rendu) :
  - Les onglets DMARC / TLS / Tous filtrent bien la liste.
  - Les colonnes Type, Organisation émettrice, Volume · échec affichent des valeurs justes.
  - Un rapport à magnitude illisible montre « — » (pas « 0 % »), un total partiel « au moins N ».

## Ce que ce plan ne fait PAS, délibérément

- **La vue détail** (bandeau de synthèse, regroupement/tri, liens d'enquête) — c'est le **cycle 2**, qui réutilise cette fondation.
- **Filtres par date / par domaine / tri des colonnes** — non retenus.
- **Colonne « Période couverte »** — la donnée est stockée (pour le cycle 2) mais pas affichée.
- **Séparer le menu latéral** — l'utilisateur a choisi des onglets dans la page ; le menu reste inchangé.
```
