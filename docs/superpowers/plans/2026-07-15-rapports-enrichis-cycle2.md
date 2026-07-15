# Rapports enrichis — Cycle 2 — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrichir la vue détail d'un rapport — bandeau de synthèse kind-aware, vue groupée par IP pour les rapports DMARC, et liens d'enquête (émetteur, domaine).

**Architecture:** Un endpoint unique `GET /reports/{id}/breakdown` (RLS-scopé) fournit le domaine et, pour DMARC, la répartition DKIM/SPF alignée + les lignes groupées par IP ; le verdict TLS se dérive côté client des champs du cycle 1. Le filtre `?reporter=` réutilise la colonne `report.reporter` du cycle 1. Le frontend reconstruit `ReportDetail` (bandeau + vue groupée) et ajoute une puce émetteur à la liste.

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy 2.0 · PostgreSQL (RLS) · pytest · React 19 + TypeScript · Vite · TanStack Query · Tailwind.

**Spec:** `docs/superpowers/specs/2026-07-15-rapports-enrichis-cycle2-design.md`

## Global Constraints

- **Base d'exécution : le cycle 1.** Ce plan consomme le code du cycle 1 (colonne `report.reporter`, `report.kind`, champs `ReportOut` : `kind`/`reporter`/`total_units`/`failing_units`/`units_partial`/`period_start`/`period_end`, filtre `?kind=`, `useReports({status,brand,kind,page})`, interface `Report` avec ces champs). **Créer la branche d'implémentation sur le cycle 1 mergé** (ou sur la branche `feat/rapports-enrichis-cycle1`). Sur `main` seul, ces symboles n'existent pas encore.
- **Isolation multitenant (CLAUDE.md)** : aucune route n'ajoute de `WHERE tenant_id` applicatif — la session `get_db` est déjà scopée par RLS. `tests/test_tenant_isolation.py` reste vert et **bloque le merge**. Un rapport d'un autre tenant → **404**, jamais 403.
- **Discipline « null ≠ 0 »** : une magnitude illisible ne s'affiche jamais « 0 »/« 0 % ». Réutiliser la convention du cycle 1 (« — » / « au moins N »).
- **Réutiliser les casts JSONB existants** : les helpers `_msgs`, `_msgs_where`, `_aligned`, `_dkim`, `_spf`, `_source_ip`, `_est_une_ligne_dmarc` sont définis **une seule fois** dans `app/api/metrics.py` (source unique, cf. ses commentaires). L'endpoint `breakdown` les **importe** plutôt que de les redéfinir.
- **`compliant`/aligné** : `compliant = Σ message_count où aligned=='pass'` ; `dkim_aligned/spf_aligned = Σ message_count où dkim/spf=='pass'`. Identique à `metrics.py`.
- **Lien domaine → MtaStsPanel : admin ET tenant sélectionné.** `MtaStsPanel` (props `{ tenantId, domain, onClose }`) est un composant admin ; son endpoint `/admin/tenants/{id}/tls-posture` est admin. Le lien n'apparaît que si `isAdmin()` **et** qu'un tenant est actif (`useTenant().tenant` non nul — sinon on n'a pas de `tenantId`).
- Commentaires et libellés en français. Messages de commit en français **sans accents**.
- **Back-end** : le conteneur `api` bake le code (pas de montage source) → pour tester du code non commité, utiliser un **run monté** depuis `infra/` (Git Bash, `MSYS_NO_PATHCONV=1` INDISPENSABLE) :
  ```bash
  MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest <chemin> -q
  ```
  Lint : `... api ruff check <chemin>`. **Ne pas** utiliser `docker compose exec api` (ne voit pas les fichiers non commités).
- **Front-end** : pas de harnais de test. Vérification = `tsc -b` + `vite build` verts :
  ```bash
  MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/frontend:/app" -w /app frontend sh -c "npm install --silent && npx tsc -b && npx vite build"
  ```

## Structure des fichiers

| Fichier | Rôle |
|---|---|
| `backend/app/api/reports.py` | **Modifier.** Endpoint `breakdown` + param `reporter` de `list_reports`. |
| `backend/tests/test_report_breakdown.py` | **Créer.** Agrégats DMARC, domaine, TLS minimal, 404 hors tenant. |
| `backend/tests/test_reports_reporter_filter.py` | **Créer.** Filtre `?reporter=`. |
| `frontend/src/api/reports.ts` | **Modifier.** `ReportBreakdown` + `useReportBreakdown`, param `reporter` de `useReports`. |
| `frontend/src/pages/ReportDetail.tsx` | **Modifier.** Bandeau de synthèse + vue groupée par IP + liens. |
| `frontend/src/pages/ReportsList.tsx` | **Modifier.** Puce filtre émetteur. |

`breakdown` renvoie un **dict simple** (comme `metrics.dmarc_sources`) — **pas** de nouveau schéma Pydantic, `schemas.py` n'est pas touché.

Ordre : 1 (breakdown) → 2 (reporter filter) → 3 (api front) → 4 (ReportDetail) → 5 (ReportsList). Task 4 consomme les hooks de Task 3 et l'endpoint de Task 1.

---

### Task 1: Endpoint `GET /reports/{id}/breakdown`

**Files:**
- Modify: `backend/app/api/reports.py`
- Test: `backend/tests/test_report_breakdown.py`

**Interfaces:**
- Consumes: `Report`, `ReportRow` (models) ; les helpers JSONB de `app.api.metrics`.
- Produces: `GET /reports/{id}/breakdown` → dict :
  - toujours `{"policy_domain": str | None}` ;
  - si `report.kind == "dmarc"` : `+ {"dkim_aligned": int, "spf_aligned": int, "sources": [{"source_ip": str, "messages": int, "compliant": int, "failing": int}]}` (sources triées par `messages` décroissant) ;
  - si `report.kind == "tls"` : rien de plus.
  - 404 si le rapport n'est pas visible du tenant.

- [ ] **Step 1: Écrire les tests d'abord**

Créer `backend/tests/test_report_breakdown.py` :

```python
"""GET /reports/{id}/breakdown : agregats par rapport, sous RLS.

Les casts JSONB sont partages avec metrics.py ; on verifie ici qu'ils sont bien
FILTRES sur ce report_id (pas sur toute la base) et que l'isolation renvoie 404.
"""
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.reports import router
from app.auth.middleware import TenantContext
from app.db.models import Email, Report, ReportRow, Tenant
from app.db.session import get_session


def _client(tenant_id):
    app = FastAPI()
    ctx = TenantContext(user="brk@test", role="tenant_viewer",
                        tenant_ids=(tenant_id,), active_tenant=tenant_id, bypass=False)

    @app.middleware("http")
    async def inject_ctx(request, call_next):
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def _dmarc_row(tid, rid, ip, count, aligned, dkim, spf):
    return ReportRow(tenant_id=tid, report_id=rid, report_date=None,
                     data={"source_ip": ip, "message_count": count, "aligned": aligned,
                           "dkim": dkim, "spf": spf, "policy_domain": "exemple.fr",
                           "reporter": "google.com"})


def _setup_dmarc():
    with get_session() as db:
        t = Tenant(domain=f"brk-{uuid.uuid4().hex[:8]}.test", name="Brk")
        db.add(t); db.flush()
        em = Email(tenant_id=t.id, message_id=f"m-{uuid.uuid4()}", from_address="x@y.test",
                   subject="s", received_at=datetime.now(timezone.utc),
                   raw_object_key="raw/x.eml", status="parsed_ok")
        db.add(em); db.flush()
        rep = Report(tenant_id=t.id, email_id=em.id, source_type="attachment", status="ok",
                     kind="dmarc", reporter="google.com", total_units=610, failing_units=110,
                     units_partial=False)
        db.add(rep); db.flush()
        # 1.1.1.1 : 400 alignes (dkim pass) + 100 non alignes ; 2.2.2.2 : 110 non alignes.
        db.add_all([
            _dmarc_row(t.id, rep.id, "1.1.1.1", 400, "pass", "pass", "fail"),
            _dmarc_row(t.id, rep.id, "1.1.1.1", 100, "fail", "fail", "fail"),
            _dmarc_row(t.id, rep.id, "2.2.2.2", 110, "fail", "fail", "fail"),
        ])
        db.commit()
        return str(t.id), str(rep.id)


def _cleanup(tid):
    with get_session() as db:
        rids = [r.id for r in db.query(Report.id).filter_by(tenant_id=tid).all()]
        db.query(ReportRow).filter(ReportRow.report_id.in_(rids)).delete(synchronize_session=False)
        db.query(Report).filter_by(tenant_id=tid).delete()
        db.query(Email).filter_by(tenant_id=tid).delete()
        db.query(Tenant).filter_by(id=tid).delete()
        db.commit()


def test_breakdown_dmarc_agrege_par_ip_et_dkim_spf():
    tid, rid = _setup_dmarc()
    try:
        b = _client(tid).get(f"/reports/{rid}/breakdown").json()
        assert b["policy_domain"] == "exemple.fr"
        assert b["dkim_aligned"] == 400        # seule la ligne dkim=pass
        assert b["spf_aligned"] == 0
        srcs = {s["source_ip"]: s for s in b["sources"]}
        assert srcs["1.1.1.1"]["messages"] == 500
        assert srcs["1.1.1.1"]["compliant"] == 400
        assert srcs["1.1.1.1"]["failing"] == 100
        assert srcs["2.2.2.2"]["messages"] == 110
        assert srcs["2.2.2.2"]["failing"] == 110
        # trie par volume decroissant
        assert b["sources"][0]["source_ip"] == "1.1.1.1"
    finally:
        _cleanup(tid)


def test_breakdown_tls_domaine_seul():
    with get_session() as db:
        t = Tenant(domain=f"brk-{uuid.uuid4().hex[:8]}.test", name="BrkTls")
        db.add(t); db.flush()
        em = Email(tenant_id=t.id, message_id=f"m-{uuid.uuid4()}", from_address="x@y.test",
                   subject="s", received_at=datetime.now(timezone.utc),
                   raw_object_key="raw/x.eml", status="parsed_ok")
        db.add(em); db.flush()
        rep = Report(tenant_id=t.id, email_id=em.id, source_type="attachment", status="ok",
                     kind="tls", reporter="microsoft.com", total_units=100, failing_units=0,
                     units_partial=False)
        db.add(rep); db.flush()
        db.add(ReportRow(tenant_id=t.id, report_id=rep.id, report_date=None,
                         data={"kind": "summary", "policy_domain": "exemple.fr",
                               "successful_sessions": 100, "failed_sessions": 0,
                               "reporter": "microsoft.com"}))
        db.commit()
        tid, rid = str(t.id), str(rep.id)
    try:
        b = _client(tid).get(f"/reports/{rid}/breakdown").json()
        assert b["policy_domain"] == "exemple.fr"
        assert "sources" not in b          # TLS : pas d'agregat DMARC
        assert "dkim_aligned" not in b
    finally:
        _cleanup(tid)


def test_breakdown_autre_tenant_404():
    tid, rid = _setup_dmarc()
    other = None
    try:
        with get_session() as db:
            t2 = Tenant(domain=f"brk-{uuid.uuid4().hex[:8]}.test", name="Autre")
            db.add(t2); db.flush(); other = str(t2.id); db.commit()
        # Le client scope sur `other` ne doit PAS voir le rapport de `tid`.
        assert _client(other).get(f"/reports/{rid}/breakdown").status_code == 404
    finally:
        _cleanup(tid)
        if other:
            with get_session() as db:
                db.query(Tenant).filter_by(id=other).delete(); db.commit()
```

- [ ] **Step 2: Lancer les tests, vérifier qu'ils échouent**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest tests/test_report_breakdown.py -q` (depuis `infra/`)
Expected: FAIL (404 sur tous — l'endpoint n'existe pas encore).

- [ ] **Step 3: Ajouter l'endpoint**

Dans `backend/app/api/reports.py`, ajouter les imports en tête (avec les imports existants) :

```python
from sqlalchemy import func

from app.api.metrics import (
    _aligned, _dkim, _est_une_ligne_dmarc, _msgs, _msgs_where, _source_ip, _spf,
)
```

(`reports.py` importe déjà `Report`, `ReportRow`, `HTTPException`, `status`, `Depends`, `get_db`. On réutilise les casts JSONB de `metrics.py` — leur source unique — plutôt que de les redéfinir.)

Puis ajouter l'endpoint (après `get_report`, avant `get_report_rows` par exemple) :

```python
@router.get("/{report_id}/breakdown")
def get_report_breakdown(report_id: str, db=Depends(get_db)):
    """Analyse d'un rapport pour le bandeau et la vue groupee. Kind-aware.

    Tout passe par la session scopee (RLS) : un rapport d'un autre tenant -> 404 (via
    db.get qui ne le voit pas), jamais 403. Les casts JSONB sont ceux de metrics.py,
    ici FILTRES sur ce report_id.
    """
    r = db.get(Report, report_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rapport introuvable")

    # policy_domain : uniforme sur le rapport, lu sur la premiere ligne qui le porte.
    domain = (db.query(ReportRow.data["policy_domain"].astext)
                .filter(ReportRow.report_id == report_id,
                        ReportRow.data["policy_domain"].astext.isnot(None))
                .limit(1).scalar())
    out: dict = {"policy_domain": domain}

    if r.kind == "dmarc":
        agg = (db.query(
                   _msgs_where(_dkim == "pass").label("dkim_aligned"),
                   _msgs_where(_spf == "pass").label("spf_aligned"))
               .filter(ReportRow.report_id == report_id, _est_une_ligne_dmarc)
               .one())
        sources = (db.query(
                       _source_ip.label("ip"),
                       func.coalesce(func.sum(_msgs), 0).label("messages"),
                       _msgs_where(_aligned == "pass").label("compliant"))
                   .filter(ReportRow.report_id == report_id, _est_une_ligne_dmarc)
                   .group_by(_source_ip)
                   .order_by(func.coalesce(func.sum(_msgs), 0).desc())
                   .all())
        out["dkim_aligned"] = int(agg.dkim_aligned)
        out["spf_aligned"] = int(agg.spf_aligned)
        out["sources"] = [{"source_ip": ip, "messages": int(m),
                           "compliant": int(c), "failing": int(m) - int(c)}
                          for ip, m, c in sources]
    return out
```

- [ ] **Step 4: Lancer les tests, vérifier qu'ils passent**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest tests/test_report_breakdown.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint + commit**

```bash
docker compose ... ruff check app/api/reports.py   # via le run monte
git add backend/app/api/reports.py backend/tests/test_report_breakdown.py
git commit -m "feat(reports): endpoint breakdown (agregats DMARC par IP + DKIM/SPF, domaine)

Kind-aware : DMARC renvoie dkim/spf alignes + sources groupees par IP ; TLS le domaine
seul (verdict derive cote client). Reutilise les casts JSONB de metrics.py, filtres sur
ce report_id. RLS : 404 hors tenant."
```

---

### Task 2: Filtre `GET /reports?reporter=`

**Files:**
- Modify: `backend/app/api/reports.py` (`list_reports`)
- Test: `backend/tests/test_reports_reporter_filter.py`

**Interfaces:**
- Consumes: colonne `Report.reporter` (cycle 1).
- Produces: `GET /reports?reporter=<org>` → filtré `Report.reporter == reporter`, combinable avec `status_f`/`kind`/`brand`.

- [ ] **Step 1: Écrire le test d'abord**

Créer `backend/tests/test_reports_reporter_filter.py` :

```python
"""GET /reports?reporter= : correspondance exacte, sous RLS."""
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.reports import router
from app.auth.middleware import TenantContext
from app.db.models import Email, Report, Tenant
from app.db.session import get_session


def _client(tenant_id):
    app = FastAPI()
    ctx = TenantContext(user="rep@test", role="tenant_viewer",
                        tenant_ids=(tenant_id,), active_tenant=tenant_id, bypass=False)

    @app.middleware("http")
    async def inject_ctx(request, call_next):
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def test_filtre_reporter():
    with get_session() as db:
        t = Tenant(domain=f"rep-{uuid.uuid4().hex[:8]}.test", name="Rep")
        db.add(t); db.flush()
        em = Email(tenant_id=t.id, message_id=f"m-{uuid.uuid4()}", from_address="x@y.test",
                   subject="s", received_at=datetime.now(timezone.utc),
                   raw_object_key="raw/x.eml", status="parsed_ok")
        db.add(em); db.flush()
        for rep_org in ("google.com", "google.com", "microsoft.com"):
            db.add(Report(tenant_id=t.id, email_id=em.id, source_type="body", status="ok",
                          kind="dmarc", reporter=rep_org, total_units=1, failing_units=0,
                          units_partial=False))
        db.commit()
        tid = str(t.id)
    try:
        c = _client(tid)
        assert c.get("/reports").json()["total"] == 3
        g = c.get("/reports?reporter=google.com").json()
        assert g["total"] == 2
        assert all(it["reporter"] == "google.com" for it in g["items"])
        assert c.get("/reports?reporter=microsoft.com").json()["total"] == 1
    finally:
        with get_session() as db:
            db.query(Report).filter_by(tenant_id=tid).delete()
            db.query(Email).filter_by(tenant_id=tid).delete()
            db.query(Tenant).filter_by(id=tid).delete()
            db.commit()
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest tests/test_reports_reporter_filter.py -q`
Expected: FAIL (`?reporter=` ignoré → renvoie 3 au lieu de 2).

- [ ] **Step 3: Ajouter le paramètre**

Dans `backend/app/api/reports.py`, remplacer la signature et le corps de `list_reports` par :

```python
@router.get("", response_model=Page[ReportOut])
def list_reports(status_f: str | None = None, brand: str | None = None,
                 kind: str | None = None, reporter: str | None = None,
                 db=Depends(get_db), pg=Depends(page_params)):
    q = db.query(Report)
    if status_f:
        q = q.filter(Report.status == status_f)
    if kind:
        q = q.filter(Report.kind == kind)
    if reporter:
        q = q.filter(Report.reporter == reporter)
    if brand:
        q = q.join(Email, Email.id == Report.email_id)\
             .filter(Email.from_address.ilike(f"%{brand}%"))
    return paginate(q.order_by(Report.created_at.desc()), *pg)
```

- [ ] **Step 4: Lancer, vérifier que ça passe (+ isolation)**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest tests/test_reports_reporter_filter.py tests/test_tenant_isolation.py -q`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
git add backend/app/api/reports.py backend/tests/test_reports_reporter_filter.py
git commit -m "feat(reports): filtre GET /reports?reporter= (correspondance exacte)

Reutilise la colonne report.reporter du cycle 1 ; se combine aux autres filtres. Isolation
RLS inchangee."
```

---

### Task 3: Front — types + `useReportBreakdown` + param `reporter`

**Files:**
- Modify: `frontend/src/api/reports.ts`

**Interfaces:**
- Consumes: `GET /reports/{id}/breakdown`, `GET /reports?reporter=` (Tasks 1-2).
- Produces:
  - `interface ReportBreakdown { policy_domain: string | null; dkim_aligned?: number; spf_aligned?: number; sources?: ReportSource[] }`
  - `interface ReportSource { source_ip: string; messages: number; compliant: number; failing: number }`
  - `useReportBreakdown(id: string)` → `ReportBreakdown`
  - `useReports` accepte `reporter?: string`.

- [ ] **Step 1: Ajouter types + hook, et le param `reporter`**

Dans `frontend/src/api/reports.ts` :

Ajouter, après l'interface `ReportRowEnvelope` (ou près des autres interfaces) :

```ts
export interface ReportSource {
  source_ip: string;
  messages: number;
  compliant: number;
  failing: number;
}

export interface ReportBreakdown {
  policy_domain: string | null;
  dkim_aligned?: number;
  spf_aligned?: number;
  sources?: ReportSource[];
}
```

Ajouter le paramètre `reporter` à `useReports` (signature + query string) — remplacer la fonction par :

```ts
export function useReports(filters: {
  status?: string; brand?: string; kind?: string; reporter?: string; page: number;
}) {
  const qs = new URLSearchParams();
  if (filters.status) qs.set("status_f", filters.status);
  if (filters.brand) qs.set("brand", filters.brand);
  if (filters.kind) qs.set("kind", filters.kind);
  if (filters.reporter) qs.set("reporter", filters.reporter);
  qs.set("page", String(filters.page));
  return useQuery({
    queryKey: ["reports", filters],
    queryFn: () => api<Page<Report>>(`/reports?${qs}`),
    placeholderData: (prev) => prev,
  });
}
```

Ajouter le hook (près de `useReportRows`) :

```ts
export const useReportBreakdown = (id: string) =>
  useQuery({
    queryKey: ["report", id, "breakdown"],
    queryFn: () => api<ReportBreakdown>(`/reports/${id}/breakdown`),
  });
```

- [ ] **Step 2: Vérification frontend**

Run (depuis `infra/`) : `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/frontend:/app" -w /app frontend sh -c "npm install --silent && npx tsc -b && npx vite build"`
Expected : `tsc` et `vite build` verts.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/reports.ts
git commit -m "feat(front): types Breakdown + useReportBreakdown + param reporter de useReports"
```

---

### Task 4: Front — bandeau de synthèse + vue groupée (`ReportDetail.tsx`)

**Files:**
- Modify: `frontend/src/pages/ReportDetail.tsx`

**Interfaces:**
- Consumes: `useReport`, `useReportBreakdown`, `useReportErrors`, `useReprocess` (`../api/reports`) ; `IpPanel`, `StatusBadge`, `MtaStsPanel` (composants) ; `isAdmin` (`../auth/session`) ; `useTenant` (`../auth/tenant`).
- Produces: rien (feuille).

- [ ] **Step 1: Réécrire `ReportDetail.tsx`**

Remplacer tout le contenu de `frontend/src/pages/ReportDetail.tsx` par (les composants `TlsTable`, `GenericTable`, `ErrorsList` sont **conservés à l'identique** depuis la version actuelle — les recopier ; seuls l'en-tête, `RowsTable` et `DmarcTable` changent) :

```tsx
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  type Report,
  type ReportBreakdown,
  useReport,
  useReportBreakdown,
  useReportErrors,
  useReportRows,
  useReprocess,
} from "../api/reports";
import { isAdmin } from "../auth/session";
import { useTenant } from "../auth/tenant";
import { IpPanel } from "../components/IpPanel";
import { MtaStsPanel } from "../components/MtaStsPanel";
import { StatusBadge } from "../components/StatusBadge";

export function ReportDetail() {
  const { id } = useParams<{ id: string }>();
  const [tab, setTab] = useState<"data" | "errors">("data");
  const report = useReport(id!);
  const breakdown = useReportBreakdown(id!);
  const errors = useReportErrors(id!);
  const reprocess = useReprocess();

  if (report.isLoading) return <p className="p-6">Chargement…</p>;
  const r = report.data!;

  return (
    <div className="p-6">
      <Synthese r={r} breakdown={breakdown.data} />

      <div className="mb-4 flex justify-end">
        <button
          onClick={() => reprocess.mutate(r.id)}
          disabled={reprocess.isPending}
          className="rounded bg-blue-600 px-3 py-1 text-white disabled:opacity-40"
        >
          {reprocess.isPending ? "…" : "Rejouer le parsing"}
        </button>
      </div>

      <div className="flex gap-4 border-b mb-4">
        <button onClick={() => setTab("data")}
                className={tab === "data" ? "border-b-2 border-blue-600 pb-1" : "pb-1"}>
          Données
        </button>
        <button onClick={() => setTab("errors")}
                className={tab === "errors" ? "border-b-2 border-blue-600 pb-1" : "pb-1"}>
          Erreurs ({errors.data?.length ?? 0})
        </button>
      </div>

      {tab === "data"
        ? <DataView r={r} breakdown={breakdown.data} loading={breakdown.isLoading} />
        : <ErrorsList errors={errors.data ?? []} />}
    </div>
  );
}

/* Bandeau de synthese : l'essentiel du rapport sans lire les lignes. Kind-aware. */
function Synthese({ r, breakdown }: { r: Report; breakdown?: ReportBreakdown }) {
  const { tenant } = useTenant();
  const [mtaSts, setMtaSts] = useState(false);
  const domain = breakdown?.policy_domain ?? null;
  // Le lien domaine -> MtaStsPanel exige un composant admin ET un tenant concret.
  const domaineCliquable = domain != null && isAdmin() && tenant != null;

  return (
    <div className="mb-4 rounded border bg-white p-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <TypeBadge kind={r.kind} />
        <h1 className="text-lg font-semibold">{r.id.slice(0, 8)}</h1>
        <StatusBadge status={r.status} />
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
        <Fait label="Émetteur">
          <Link to={`/reports?reporter=${encodeURIComponent(r.reporter ?? "")}`}
                className="text-blue-600 hover:underline">
            {r.reporter ?? "—"}
          </Link>
        </Fait>
        <Fait label="Domaine">
          {domaineCliquable ? (
            <button onClick={() => setMtaSts(true)} className="text-blue-600 hover:underline">
              {domain}
            </button>
          ) : (domain ?? "—")}
        </Fait>
        <Fait label="Période">
          {r.period_start ?? "?"} → {r.period_end ?? "?"}
        </Fait>
        <Fait label="Volume">{fmtVolume(r)}</Fait>
        <Fait label="Taux d'échec">{fmtRate(r)}</Fait>
      </dl>

      {r.kind === "dmarc" && breakdown && r.total_units !== null && (
        <div className="mt-3 space-y-2">
          <Barre label="DKIM aligné" value={breakdown.dkim_aligned ?? 0} total={r.total_units} />
          <Barre label="SPF aligné" value={breakdown.spf_aligned ?? 0} total={r.total_units} />
        </div>
      )}

      {r.kind === "tls" && <VerdictTls r={r} />}

      {mtaSts && tenant && domain && (
        <MtaStsPanel tenantId={tenant} domain={domain} onClose={() => setMtaSts(false)} />
      )}
    </div>
  );
}

/* Verdict TLS derive des champs du cycle 1 : sur pour enforce = aucun echec ET total
   entierement lisible. On ne dit jamais "sur" sur une magnitude partielle/inconnue. */
function VerdictTls({ r }: { r: Report }) {
  const sur = r.total_units !== null && !r.units_partial && r.failing_units === 0;
  return (
    <div className={`mt-3 rounded border p-3 text-sm ${
      sur ? "border-green-200 bg-green-50 text-green-900"
          : "border-red-200 bg-red-50 text-red-900"}`}>
      {sur
        ? "Chiffrement vérifié : sûr de passer en application (enforce)."
        : "Des sessions échouent ou sont illisibles — à corriger avant d'appliquer."}
    </div>
  );
}

function DataView({ r, breakdown, loading }:
    { r: Report; breakdown?: ReportBreakdown; loading: boolean }) {
  // DMARC : vue groupee par IP (breakdown). TLS/generique : rendu ligne a ligne existant.
  // Chaque branche gere son propre etat `ip`/`IpPanel` (branches mutuellement exclusives).
  if (r.kind === "dmarc") {
    return <DmarcSources sources={breakdown?.sources ?? []} loading={loading} />;
  }
  return <RowsLegacy reportId={r.id} />;
}

function DmarcSources({ sources, loading }:
    { sources: NonNullable<ReportBreakdown["sources"]>; loading: boolean }) {
  const [ip, setIp] = useState<string | null>(null);
  if (loading) return <p>Chargement…</p>;
  if (!sources.length) return <p className="text-gray-500">Aucune source.</p>;
  return (
    <>
      <SourcesTable sources={sources} onSelectIp={setIp} />
      {ip && <IpPanel ip={ip} onClose={() => setIp(null)} />}
    </>
  );
}

/* Vue groupee par IP (DMARC) : une ligne par IP source, coherente avec le tableau
   Sources de la Vue d'ensemble. L'IP est le point d'entree de l'enquete -> cliquable. */
function SourcesTable({ sources, onSelectIp }:
    { sources: NonNullable<ReportBreakdown["sources"]>; onSelectIp: (ip: string) => void }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="border-b text-left text-gray-500">
          <tr>
            <th className="py-2 pr-4">IP source</th>
            <th className="py-2 pr-4 text-right">Messages</th>
            <th className="py-2 pr-4 text-right">Conformes</th>
            <th className="py-2 pr-4 text-right">En échec</th>
          </tr>
        </thead>
        <tbody>
          {sources.map((s) => (
            <tr key={s.source_ip} className="border-b">
              <td className="py-1 pr-4">
                <button onClick={() => onSelectIp(s.source_ip)}
                        className="font-mono text-blue-600 hover:underline">
                  {s.source_ip}
                </button>
              </td>
              <td className="py-1 pr-4 text-right tabular-nums">{s.messages}</td>
              <td className="py-1 pr-4 text-right tabular-nums text-green-700">{s.compliant}</td>
              <td className="py-1 pr-4 text-right tabular-nums text-red-700">
                {s.failing || "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

**PUIS** ajouter `RowsLegacy` — c'est l'ancien `RowsTable` **sans** la branche DMARC (le composant `DmarcTable` disparaît entièrement : code mort, DMARC passe désormais par `DmarcSources`/`SourcesTable`). Il garde son propre état `ip` + `IpPanel` pour le cas TLS :

```tsx
/* TLS et generique : rendu ligne a ligne (inchange). La branche DMARC de l'ancien
   RowsTable a disparu -- DMARC passe par DmarcSources. */
function RowsLegacy({ reportId }: { reportId: string }) {
  const [page, setPage] = useState(1);
  const [ip, setIp] = useState<string | null>(null);
  const { data, isLoading } = useReportRows(reportId, page);
  if (isLoading) return <p>Chargement…</p>;
  const items = data!.items;
  if (!items.length) return <p className="text-gray-500">Aucune donnée.</p>;
  const rows = items.map((r) => r.data);
  const isTls = "kind" in rows[0] && "policy_domain" in rows[0];
  return (
    <>
      {isTls ? <TlsTable rows={rows} onSelectIp={setIp} /> : <GenericTable rows={rows} />}
      <div className="flex gap-2 mt-4 items-center">
        <button disabled={page <= 1} onClick={() => setPage(page - 1)} className="disabled:opacity-40">←</button>
        <span className="text-sm">Page {page} · {data?.total} lignes</span>
        <button disabled={items.length < 50} onClick={() => setPage(page + 1)} className="disabled:opacity-40">→</button>
      </div>
      {ip && <IpPanel ip={ip} onClose={() => setIp(null)} />}
    </>
  );
}
```

(`useReportRows` est déjà dans le bloc d'import en tête ci-dessus — `RowsLegacy` en a besoin.)

Enfin, **conserver tels quels** (verbatim, ils ne changent pas) les composants `TlsTable`, `GenericTable` et `ErrorsList` déjà présents dans le fichier actuel, et ajouter en bas les helpers de présentation :

```tsx
function TypeBadge({ kind }: { kind: Report["kind"] }) {
  const tls = kind === "tls";
  return (
    <span className={`rounded px-1.5 py-0.5 text-xs ${
      tls ? "bg-purple-100 text-purple-800" : "bg-blue-100 text-blue-800"}`}>
      {tls ? "TLS" : "DMARC"}
    </span>
  );
}

function Fait({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2">
      <dt className="text-gray-500">{label}</dt>
      <dd className="min-w-0 break-words font-medium">{children}</dd>
    </div>
  );
}

function Barre({ label, value, total }: { label: string; value: number; total: number }) {
  const pct = total ? Math.round((100 * value) / total) : 0;
  return (
    <div>
      <div className="flex justify-between text-xs text-gray-600">
        <span>{label}</span>
        <span className="tabular-nums">{total ? `${pct} %` : "—"}</span>
      </div>
      <div className="mt-1 h-1.5 rounded bg-gray-100">
        <div className="h-1.5 rounded bg-emerald-500" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/* Convention du cycle 1 : « — » si le total est illisible (jamais « 0 »/« 0 % ») ;
   « au moins N » si le total n'est qu'un minorant. */
function fmtVolume(r: Report): string {
  if (r.total_units === null) return "—";
  const n = r.total_units.toLocaleString("fr-FR");
  return r.units_partial ? `au moins ${n}` : n;
}

function fmtRate(r: Report): string {
  if (r.total_units === null || r.total_units === 0 || r.units_partial) return "—";
  const pct = Math.round((100 * (r.failing_units ?? 0)) / r.total_units);
  return `${pct} % en échec`;
}
```

- [ ] **Step 2: Vérification frontend**

Run : `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/frontend:/app" -w /app frontend sh -c "npm install --silent && npx tsc -b && npx vite build"`
Expected : `tsc` sans erreur (types `Report`/`ReportBreakdown` cohérents ; `RowsLegacy` sans branche DMARC morte), `vite build` réussi.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/ReportDetail.tsx
git commit -m "feat(front): vue detail enrichie (bandeau de synthese + vue groupee par IP)

Bandeau kind-aware (emetteur cliquable, domaine admin -> MtaStsPanel, barres DKIM/SPF pour
DMARC, verdict pour TLS). Onglet Donnees DMARC : liste brute remplacee par la vue groupee
par IP (breakdown.sources), IP cliquable vers le panneau d enquete. TLS/generique inchanges."
```

---

### Task 5: Front — puce filtre émetteur (`ReportsList.tsx`)

**Files:**
- Modify: `frontend/src/pages/ReportsList.tsx`

**Interfaces:**
- Consumes: `useReports({..., reporter})` (Task 3).
- Produces: rien (feuille).

- [ ] **Step 1: Lire `?reporter=`, le passer, afficher une puce**

Dans `frontend/src/pages/ReportsList.tsx` :

Lire le paramètre (à côté de `status`/`brand`/`kind`) :

```tsx
  const reporter = sp.get("reporter") ?? "";
```

Le passer à `useReports` :

```tsx
  const { data, isLoading } = useReports({ status, brand, kind, reporter, page });
```

Et, **juste au-dessus de la ligne des filtres** (`<div className="flex gap-3 mb-4">`), afficher une puce quand un émetteur est filtré :

```tsx
      {reporter && (
        <div className="mb-3">
          <button
            onClick={() => set("reporter", "")}
            className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-3 py-1 text-sm text-blue-800 hover:bg-blue-100"
          >
            Émetteur : {reporter}
            <span aria-hidden className="text-blue-500">✕</span>
          </button>
        </div>
      )}
```

(Le helper `set(k, v)` existant met `v` vide → `delete` la clé + remet `page=1` : cliquer la puce retire le filtre.)

- [ ] **Step 2: Vérification frontend**

Run : `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/frontend:/app" -w /app frontend sh -c "npm install --silent && npx tsc -b && npx vite build"`
Expected : `tsc` et `vite build` verts.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/ReportsList.tsx
git commit -m "feat(front): puce de filtre par emetteur sur la liste des rapports

Lit ?reporter= (pose par le lien emetteur de la vue detail) et affiche une puce cliquable
pour retirer le filtre."
```

---

## Vérification finale

- [ ] `docker compose ... api pytest` (run monté) — toute la suite verte, dont `test_report_breakdown`, `test_reports_reporter_filter`, et **`test_tenant_isolation` (bloquant)**.
- [ ] `ruff check app` propre.
- [ ] Vérification frontend — `tsc -b` + `vite build` verts.
- [ ] **Contrôle réel navigateur** :
  - Bandeau : émetteur/type/période/volume/taux corrects ; DMARC → barres DKIM/SPF ; TLS → verdict.
  - Émetteur cliquable → liste filtrée + puce ; la puce retire le filtre.
  - Domaine cliquable **en tant qu'admin avec un tenant sélectionné** → `MtaStsPanel` s'ouvre ; en lecteur simple, domaine en texte.
  - Onglet Données DMARC → vue groupée par IP, IP cliquable → panneau d'enquête ; TLS → sessions + échecs inchangés.

## Ce que ce plan ne fait PAS, délibérément

- **Alertes liées au domaine** dans le bandeau — non retenu.
- **Garder la liste brute DMARC** — remplacée par le groupé.
- **Dénormaliser `policy_domain`** — lu à la volée par `breakdown`, pas de migration.
- **Tri interactif des colonnes** — le groupé est trié par volume.
