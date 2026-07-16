# API publique v1 — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Exposer une API publique versionnée (`/api/v1`) permettant à des programmes tiers de lire (domaines, rapports agrégés, métriques, quarantaine) et de créer des domaines, via des clés API plateforme ou par-domaine, sans jamais rompre l'isolation multitenant.

**Architecture:** Une table d'auth `api_key` (sans RLS, comme `app_user`). Le `TenantMiddleware` détecte un Bearer `sk_…`, résout la clé en bypass (comme `login` résout un user) et produit un `TenantContext` — scopé RLS pour une clé domaine, `bypass` pour une clé plateforme — borné à la surface `/api/v1` par un nouveau champ `api_key_scope`. Un routeur `app/api/public.py` réutilise la logique de requête existante (metrics, create_tenant). La gestion des clés passe par des routes admin JWT + une section UI dans Paramètres.

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy 2.0 · Alembic · PostgreSQL 16 (RLS) · React 19 + TypeScript · pytest.

## Global Constraints

- `api_key` est une table d'**auth**, traitée comme `app_user` : **PAS de RLS**, GRANT explicites `SELECT, INSERT, UPDATE, DELETE` à `app_api` **et** `app_worker`. Jamais lisible par une session cliente (uniquement frontière d'auth en bypass + routes admin).
- Une **clé domaine** produit un `TenantContext` **strictement identique** à celui d'un utilisateur tenant (`bypass=False`, `active_tenant` posé). Aucune route de lecture n'ajoute de `WHERE tenant_id` applicatif sur les tables **RLS** — SAUF sur la table `tenant` elle-même, qui n'a pas de RLS et doit donc être filtrée explicitement quand `not ctx.bypass`.
- Une **clé plateforme** = `bypass` comme `platform_admin`, honore `X-Tenant-Id` à l'identique, mais est **bornée à `/api/v1`** par `api_key_scope` (jamais `/admin`, `/auth`).
- Secret : `sk_plat_`/`sk_dom_` + `secrets.token_urlsafe(32)`, haché en **SHA-256 hex**, jamais restocké en clair, rendu **une seule fois**.
- Le champ ajouté à `TenantContext` porte un **défaut** (`api_key_scope: str | None = None`) pour ne casser aucune construction existante (tests, `_build_context`).
- **Routage `/api/v1` vs `/v1`** : nginx (`frontend/nginx.conf`) retire le préfixe `/api` (`rewrite ^/api/(.*)$ /$1`). L'URL **externe** est `/api/v1/…` (ce que les tiers appellent), mais le backend voit `/v1/…`. Donc : routeur préfixé **`/v1`**, garde middleware `startswith("/v1/")`, et les **tests** (montés sans nginx) appellent `/v1/…`. Seuls les exemples `curl` vers l'hôte réel utilisent l'URL externe `/api/v1/…`.
- `tests/test_tenant_isolation.py` reste **vert et bloquant** (invariant #7) : les cas clé API y sont ajoutés.
- Lint CI = `ruff check app scripts tests` (couvre les tests). Front = `tsc -b` + `vite build`.

---

### Task 1 : Table `api_key` (modèle + migration 0014)

**Files:**
- Modify: `backend/app/db/models.py` (ajouter la classe `ApiKey`)
- Create: `backend/migrations/versions/0014_api_key.py`
- Test: `backend/tests/test_api_key_model.py`

**Interfaces:**
- Produces: `ApiKey` (table `api_key`) — colonnes `id, tenant_id (NULL=plateforme), scope, prefix, key_hash (UNIQUE), label, created_at, created_by, last_used_at, revoked_at`.

- [ ] **Step 1: Écrire le test (échoue)**

`backend/tests/test_api_key_model.py` :
```python
"""La table api_key existe, est insérable, et son unicité de key_hash tient."""
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import ApiKey
from app.db.session import get_session


def test_api_key_insert_and_unique_hash():
    h = f"hash-{uuid.uuid4().hex}"
    made = []
    with get_session() as db:
        k = ApiKey(scope="platform", prefix="sk_plat_ab12", key_hash=h,
                   label="test", created_by="admin@test")
        db.add(k)
        db.commit()
        made.append(k.id)
    try:
        with get_session() as db:
            db.add(ApiKey(scope="platform", prefix="sk_plat_zz99", key_hash=h,
                          label="dup", created_by="admin@test"))
            with pytest.raises(IntegrityError):
                db.commit()
    finally:
        with get_session() as db:
            db.query(ApiKey).filter(ApiKey.id.in_(made)).delete(synchronize_session=False)
            db.commit()
```

- [ ] **Step 2: Lancer le test — échoue** (`ImportError: cannot import name 'ApiKey'`)

Run: `MSYS_NO_PATHCONV=1 docker compose -f infra/docker-compose.yml run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest tests/test_api_key_model.py -q`
Expected: FAIL (ImportError).

- [ ] **Step 3: Ajouter le modèle**

Dans `backend/app/db/models.py`, après la classe `UserTenant` (section « Tenants & utilisateurs ») :
```python
class ApiKey(Base):
    __tablename__ = "api_key"
    id: Mapped[uuid.UUID] = _uuid_pk()
    # NULL = clé PLATEFORME (cross-tenant) ; renseigné = clé PAR-DOMAINE (scopée RLS).
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenant.id"), nullable=True)
    scope: Mapped[str] = mapped_column(Text, nullable=False)          # 'platform' | 'domain'
    prefix: Mapped[str] = mapped_column(Text, nullable=False)         # début lisible du secret
    key_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)  # SHA-256 hex
    label: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

- [ ] **Step 4: Écrire la migration**

`backend/migrations/versions/0014_api_key.py` (style aligné sur `0013` ; UUID façon `0001_schema`) :
```python
"""table api_key (cles API plateforme + par-domaine) — table d'auth, SANS RLS

Revision ID: 0014_api_key
Revises: 0013_tenant_alert_email
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0014_api_key"
down_revision = "0013_tenant_alert_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_key",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=True),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("key_hash", name="uq_api_key_hash"),
        # scope='platform' <=> tenant_id NULL. L'egalite de deux booleens est valide en PG.
        sa.CheckConstraint("(scope = 'platform') = (tenant_id IS NULL)",
                           name="ck_api_key_scope_tenant"),
    )
    op.create_index("ix_api_key_tenant_id", "api_key", ["tenant_id"])
    # api_key est une table d'AUTH (comme app_user) : PAS de RLS. GRANT explicites.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON api_key TO app_api;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON api_key TO app_worker;")


def downgrade() -> None:
    op.drop_index("ix_api_key_tenant_id", table_name="api_key")
    op.drop_table("api_key")
```

- [ ] **Step 5: Appliquer la migration**

Run: `MSYS_NO_PATHCONV=1 docker compose -f infra/docker-compose.yml run --rm --no-deps -e DATABASE_URL_MIGRATE=postgresql://postgres:postgres@postgres:5432/reports -v "D:/code/dmarc/backend:/app" -w /app api alembic upgrade head`
Expected: `Running upgrade 0013_tenant_alert_email -> 0014_api_key`.

- [ ] **Step 6: Lancer le test — passe**

Run: (même commande qu'au Step 2) → PASS.

- [ ] **Step 7: Commit**
```bash
git add backend/app/db/models.py backend/migrations/versions/0014_api_key.py backend/tests/test_api_key_model.py
git commit -m "feat(api): table api_key (auth, sans RLS) + migration 0014"
```

---

### Task 2 : Résolution des clés à la frontière (service + middleware)

**Files:**
- Create: `backend/app/services/api_keys.py`
- Modify: `backend/app/auth/middleware.py` (champ `api_key_scope`, branche `sk_`)
- Test: `backend/tests/test_api_key_auth.py`

**Interfaces:**
- Consumes: `ApiKey` (Task 1).
- Produces:
  - `app.services.api_keys.generate_key(scope: str) -> tuple[str, str, str]` renvoyant `(secret_clair, prefix, key_hash)`.
  - `app.services.api_keys.hash_secret(secret: str) -> str` (SHA-256 hex).
  - `app.services.api_keys.resolve(secret: str) -> ResolvedKey | None` (`ResolvedKey(id, tenant_id, scope, prefix)`), met à jour `last_used_at`.
  - `TenantContext` gagne `api_key_scope: str | None = None`.

- [ ] **Step 1: Écrire le test (échoue)**

`backend/tests/test_api_key_auth.py` :
```python
"""Résolution d'une clé API et construction du contexte au middleware."""
import uuid

import pytest

from app.auth.middleware import TenantMiddleware
from app.db.models import ApiKey, Tenant
from app.db.session import get_session
from app.services import api_keys


class FakeURL:
    def __init__(self, path): self.path = path


class FakeRequest:
    def __init__(self, path, headers=None):
        self.url = FakeURL(path)
        self.headers = headers or {}


@pytest.fixture
def platform_key():
    secret, prefix, h = api_keys.generate_key("platform")
    with get_session() as db:
        k = ApiKey(scope="platform", prefix=prefix, key_hash=h, label="t", created_by="a@t")
        db.add(k); db.commit(); kid = k.id
    yield secret
    with get_session() as db:
        db.query(ApiKey).filter_by(id=kid).delete(); db.commit()


@pytest.fixture
def domain_key():
    with get_session() as db:
        t = Tenant(domain=f"k-{uuid.uuid4().hex[:8]}.test", name="K"); db.add(t); db.flush()
        secret, prefix, h = api_keys.generate_key("domain")
        k = ApiKey(scope="domain", tenant_id=t.id, prefix=prefix, key_hash=h, label="t", created_by="a@t")
        db.add(k); db.commit(); tid, kid = str(t.id), k.id
    yield secret, tid
    with get_session() as db:
        db.query(ApiKey).filter_by(id=kid).delete()
        db.query(Tenant).filter_by(id=tid).delete(); db.commit()


def test_generate_and_hash_roundtrip():
    secret, prefix, h = api_keys.generate_key("domain")
    assert secret.startswith("sk_dom_") and prefix == secret[:14]
    assert api_keys.hash_secret(secret) == h and len(h) == 64


def test_resolve_unknown_returns_none():
    assert api_keys.resolve("sk_dom_" + "x" * 40) is None


def test_platform_context_is_bypass(platform_key):
    ctx = TenantMiddleware._build_api_key_context(FakeRequest("/v1/domains"), platform_key)
    assert ctx.api_key_scope == "platform" and ctx.bypass and ctx.role == "platform_admin"


def test_domain_context_is_scoped(domain_key):
    secret, tid = domain_key
    ctx = TenantMiddleware._build_api_key_context(FakeRequest("/v1/reports"), secret)
    assert ctx.api_key_scope == "domain" and not ctx.bypass
    assert ctx.active_tenant == tid and ctx.tenant_ids == (tid,)


def test_platform_key_honors_x_tenant_id(platform_key):
    ctx = TenantMiddleware._build_api_key_context(
        FakeRequest("/v1/reports", {"X-Tenant-Id": "abc"}), platform_key)
    assert ctx.api_key_scope == "platform" and ctx.active_tenant == "abc" and not ctx.bypass


def test_api_key_blocked_outside_api_v1(domain_key):
    secret, _ = domain_key
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        TenantMiddleware._build_api_key_context(FakeRequest("/admin/tenants"), secret)
    assert e.value.status_code == 403


def test_revoked_key_returns_none(domain_key):
    secret, _ = domain_key
    from datetime import datetime, timezone
    with get_session() as db:
        db.query(ApiKey).filter_by(key_hash=api_keys.hash_secret(secret)).update(
            {"revoked_at": datetime.now(timezone.utc)}); db.commit()
    assert api_keys.resolve(secret) is None
```

- [ ] **Step 2: Lancer — échoue** (`ModuleNotFoundError: app.services.api_keys`)

Run: `MSYS_NO_PATHCONV=1 docker compose -f infra/docker-compose.yml run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest tests/test_api_key_auth.py -q`
Expected: FAIL.

- [ ] **Step 3: Écrire le service**

`backend/app/services/api_keys.py` :
```python
"""Clés API : génération, hachage, résolution à la frontière d'auth.

La résolution lit en plan worker (BYPASSRLS), exactement comme `login` résout un user :
c'est la seule lecture cross-tenant admise avant l'établissement du contexte tenant.
"""
import hashlib
import secrets as _secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from app.db.models import ApiKey
from app.db.session import get_session

_PREFIX = {"platform": "sk_plat_", "domain": "sk_dom_"}


@dataclass(frozen=True)
class ResolvedKey:
    id: str
    tenant_id: str | None
    scope: str
    prefix: str


def hash_secret(secret: str) -> str:
    """SHA-256 hex. Suffisant : le secret est un aléa 256 bits (pas un mot de passe)."""
    return hashlib.sha256(secret.encode()).hexdigest()


def generate_key(scope: str) -> tuple[str, str, str]:
    """(secret_en_clair, prefix, key_hash). Le secret n'est jamais restocké en clair."""
    secret = _PREFIX[scope] + _secrets.token_urlsafe(32)
    return secret, secret[:14], hash_secret(secret)


def resolve(secret: str) -> ResolvedKey | None:
    """None si la clé est inconnue ou révoquée. Met à jour last_used_at (best-effort)."""
    with get_session() as db:
        key = (db.query(ApiKey)
                 .filter(ApiKey.key_hash == hash_secret(secret), ApiKey.revoked_at.is_(None))
                 .first())
        if key is None:
            return None
        key.last_used_at = datetime.now(timezone.utc)
        out = ResolvedKey(id=str(key.id),
                          tenant_id=str(key.tenant_id) if key.tenant_id else None,
                          scope=key.scope, prefix=key.prefix)
        db.commit()
        return out
```

- [ ] **Step 4: Étendre le middleware**

Dans `backend/app/auth/middleware.py` :

(a) Ajouter le champ au dataclass (défaut → aucune construction existante ne casse) :
```python
@dataclass(frozen=True)
class TenantContext:
    user: str
    role: str
    tenant_ids: tuple[str, ...]
    active_tenant: str | None
    bypass: bool
    api_key_scope: str | None = None   # None = principal JWT ; 'platform'|'domain' = clé API
```

(b) Dans `dispatch`, remplacer le bloc `token = self._bearer(request)` … `request.state.tenant = self._build_context(request, claims)` par :
```python
            token = self._bearer(request)
            if token.startswith("sk_"):
                request.state.tenant = self._build_api_key_context(request, token)
            else:
                try:
                    claims = jwt.decode(
                        token, settings.jwt_public_key, algorithms=["RS256"],
                        audience=settings.jwt_audience, issuer=settings.jwt_issuer,
                    )
                except jwt.PyJWTError as exc:
                    raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"JWT invalide: {exc}")
                request.state.tenant = self._build_context(request, claims)
```

(c) Ajouter la méthode (une clé API n'est jamais admise hors `/api/v1`) :
```python
    @staticmethod
    def _build_api_key_context(request: Request, token: str) -> TenantContext:
        # Import local : évite un cycle d'import au chargement du module.
        from app.services.api_keys import resolve

        if not request.url.path.startswith("/v1/"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "clé API limitée à /api/v1")
        key = resolve(token)
        if key is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "clé API invalide")
        user = f"apikey:{key.prefix}"
        if key.scope == "platform":
            # Comme platform_admin : bypass par défaut, ou scopé si X-Tenant-Id fourni.
            wanted = request.headers.get("X-Tenant-Id")
            return TenantContext(user=user, role="platform_admin", tenant_ids=(),
                                 active_tenant=wanted, bypass=(wanted is None),
                                 api_key_scope="platform")
        return TenantContext(user=user, role="tenant_viewer", tenant_ids=(key.tenant_id,),
                             active_tenant=key.tenant_id, bypass=False, api_key_scope="domain")
```

- [ ] **Step 5: Lancer — passe**

Run: (même commande qu'au Step 2) → PASS (7 tests).

- [ ] **Step 6: Commit**
```bash
git add backend/app/services/api_keys.py backend/app/auth/middleware.py backend/tests/test_api_key_auth.py
git commit -m "feat(api): resolution des cles API au middleware (scope plateforme/domaine)"
```

---

### Task 3 : Routeur public — lectures domaines / rapports / métriques

**Files:**
- Create: `backend/app/api/public.py`
- Modify: `backend/app/main.py` (enregistrer le routeur ; retirer `/api/v1` d'un éventuel public path — non nécessaire ici)
- Test: `backend/tests/test_public_reads.py`

**Interfaces:**
- Consumes: `get_db`, `get_tenant_ctx` (deps), `TenantContext.api_key_scope` (Task 2), helpers `metrics.dmarc_summary`, `metrics.dmarc_timeseries`.
- Produces: `router` (prefix `/api/v1`), deps `require_platform`. Routes `GET /api/v1/domains|reports|metrics`.

- [ ] **Step 1: Écrire le test (échoue)**

`backend/tests/test_public_reads.py` — on monte l'app avec le **vrai** `TenantMiddleware` et on s'authentifie avec de vraies clés (exerce la RLS de bout en bout) :
```python
"""Lectures /api/v1 : une clé domaine ne voit que son domaine ; une clé plateforme voit tout."""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import public
from app.auth.middleware import TenantMiddleware
from app.db.models import ApiKey, Email, Report, Tenant
from app.db.session import get_session
from app.services import api_keys


@pytest.fixture
def app_client():
    app = FastAPI()
    app.add_middleware(TenantMiddleware)
    app.include_router(public.router)
    return TestClient(app)


@pytest.fixture
def two_tenants_keys():
    made = {}
    with get_session() as db:
        for k in ("a", "b"):
            t = Tenant(domain=f"pub-{k}-{uuid.uuid4().hex[:6]}.test", name=k.upper())
            db.add(t); db.flush()
            em = Email(tenant_id=t.id, message_id=f"m-{uuid.uuid4()}",
                       received_at=datetime.now(timezone.utc), raw_object_key="x", status="parsed_ok")
            db.add(em); db.flush()
            db.add(Report(tenant_id=t.id, email_id=em.id, source_type="body", status="ok", kind="dmarc"))
            secret, prefix, h = api_keys.generate_key("domain")
            db.add(ApiKey(scope="domain", tenant_id=t.id, prefix=prefix, key_hash=h,
                          label=k, created_by="a@t"))
            made[k] = {"tid": str(t.id), "domain": t.domain, "key": secret, "eid": em.id}
        psecret, pprefix, ph = api_keys.generate_key("platform")
        db.add(ApiKey(scope="platform", prefix=pprefix, key_hash=ph, label="p", created_by="a@t"))
        db.commit()
    made["platform_key"] = psecret
    yield made
    with get_session() as db:
        for k in ("a", "b"):
            tid, eid = made[k]["tid"], made[k]["eid"]
            db.query(ApiKey).filter_by(tenant_id=tid).delete()
            db.query(Report).filter_by(tenant_id=tid).delete()
            db.query(Email).filter_by(id=eid).delete()
            db.query(Tenant).filter_by(id=tid).delete()
        db.query(ApiKey).filter_by(key_hash=api_keys.hash_secret(made["platform_key"])).delete()
        db.commit()


def _auth(secret): return {"Authorization": f"Bearer {secret}"}


def test_domain_key_sees_only_its_domain(app_client, two_tenants_keys):
    m = two_tenants_keys
    r = app_client.get("/v1/domains", headers=_auth(m["a"]["key"]))
    assert r.status_code == 200
    domains = [d["domain"] for d in r.json()]
    assert domains == [m["a"]["domain"]]  # uniquement A


def test_platform_key_sees_all_domains(app_client, two_tenants_keys):
    m = two_tenants_keys
    r = app_client.get("/v1/domains", headers=_auth(m["platform_key"]))
    assert r.status_code == 200
    domains = {d["domain"] for d in r.json()}
    assert {m["a"]["domain"], m["b"]["domain"]} <= domains


def test_reports_and_metrics_scoped(app_client, two_tenants_keys):
    m = two_tenants_keys
    assert app_client.get("/v1/reports", headers=_auth(m["a"]["key"])).status_code == 200
    assert app_client.get("/v1/metrics", headers=_auth(m["a"]["key"])).status_code == 200


def test_no_auth_is_401(app_client):
    assert app_client.get("/v1/domains").status_code == 401
```

- [ ] **Step 2: Lancer — échoue** (`ModuleNotFoundError: app.api.public`)

Run: `MSYS_NO_PATHCONV=1 docker compose -f infra/docker-compose.yml run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest tests/test_public_reads.py -q`

- [ ] **Step 3: Écrire le routeur**

`backend/app/api/public.py` :
```python
"""API publique v1 — surface stable pour programmes tiers (clés API).

Lectures scopées par la session (`get_db`) : une clé domaine ne voit que son tenant via
la RLS. La table `tenant` n'ayant PAS de RLS, on la filtre explicitement quand la session
n'est pas en bypass. Les agrégats réutilisent les helpers de `metrics` (pas de duplication).
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func

from app.api import metrics as metrics_api
from app.auth.deps import get_db, get_tenant_ctx
from app.db.models import Report, Tenant

router = APIRouter(prefix="/v1", tags=["public"])


def require_platform(ctx=Depends(get_tenant_ctx)):
    """Autorise une clé plateforme, ou un admin JWT en vue globale. Sinon 403."""
    is_platform_key = ctx.api_key_scope == "platform"
    is_admin_user = ctx.api_key_scope is None and ctx.role == "platform_admin"
    if not (is_platform_key or is_admin_user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "réservé aux clés plateforme")
    return ctx


@router.get("/domains")
def list_domains(db=Depends(get_db), ctx=Depends(get_tenant_ctx)):
    stats = dict(
        (tid, (n, last)) for tid, n, last in
        db.query(Report.tenant_id, func.count(Report.id), func.max(Report.created_at))
          .group_by(Report.tenant_id).all()
    )
    q = db.query(Tenant)
    # tenant n'a pas de RLS : une session scopée doit filtrer explicitement sur son tenant.
    if not ctx.bypass and ctx.active_tenant:
        q = q.filter(Tenant.id == ctx.active_tenant)
    out = []
    for t in q.order_by(Tenant.domain).all():
        reports, last = stats.get(t.id, (0, None))
        out.append({
            "id": str(t.id), "domain": t.domain, "name": t.name, "status": t.status,
            "reports": reports, "last_report_at": last.isoformat() if last else None,
            "alert_email": t.alert_email,
        })
    return out


@router.get("/reports")
def reports_summary(days: int = Query(30, ge=1, le=365), db=Depends(get_db)):
    """Agrégats DMARC sur la fenêtre (réutilise metrics.dmarc_summary, scopé par la session)."""
    return metrics_api.dmarc_summary(days=days, db=db)


@router.get("/metrics")
def metrics_timeseries(days: int = Query(30, ge=1, le=365), db=Depends(get_db)):
    """Série quotidienne conforme/échoué (réutilise metrics.dmarc_timeseries)."""
    return metrics_api.dmarc_timeseries(days=days, db=db)
```

- [ ] **Step 4: Enregistrer le routeur**

Dans `backend/app/main.py`, ajouter l'import et l'inclusion (après `admin.router`) :
```python
from app.api import public
...
app.include_router(public.router)
```

- [ ] **Step 5: Lancer — passe**

Run: (même commande qu'au Step 2) → PASS.

- [ ] **Step 6: Commit**
```bash
git add backend/app/api/public.py backend/app/main.py backend/tests/test_public_reads.py
git commit -m "feat(api): routeur public /api/v1 (domaines, rapports, metriques)"
```

---

### Task 4 : Routeur public — quarantaine (plateforme) + création de domaine (plateforme)

**Files:**
- Modify: `backend/app/api/public.py` (ajouter `GET /quarantine`, `POST /domains`)
- Test: `backend/tests/test_public_write.py`

**Interfaces:**
- Consumes: `require_platform` (Task 3), `TenantIn` + logique de `create_tenant` (admin), `ensure_tenant`, `tenant_scoped_session`, `Email`.
- Produces: `GET /api/v1/quarantine`, `POST /api/v1/domains`.

- [ ] **Step 1: Écrire le test (échoue)**

`backend/tests/test_public_write.py` :
```python
"""Écriture/lecture réservées à la clé plateforme."""
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import public
from app.auth.middleware import TenantMiddleware
from app.db.models import ApiKey, Tenant, TenantMatchingRule
from app.db.session import get_session
from app.services import api_keys


@pytest.fixture
def app_client():
    app = FastAPI(); app.add_middleware(TenantMiddleware); app.include_router(public.router)
    return TestClient(app)


@pytest.fixture
def keys():
    made = {}
    with get_session() as db:
        s_p, p_p, h_p = api_keys.generate_key("platform")
        db.add(ApiKey(scope="platform", prefix=p_p, key_hash=h_p, label="p", created_by="a@t"))
        t = Tenant(domain=f"dom-{uuid.uuid4().hex[:6]}.test", name="D"); db.add(t); db.flush()
        s_d, p_d, h_d = api_keys.generate_key("domain")
        db.add(ApiKey(scope="domain", tenant_id=t.id, prefix=p_d, key_hash=h_d, label="d", created_by="a@t"))
        db.commit(); made = {"platform": s_p, "domain": s_d, "tid": str(t.id)}
    yield made
    with get_session() as db:
        db.query(ApiKey).filter_by(key_hash=api_keys.hash_secret(made["platform"])).delete()
        db.query(ApiKey).filter_by(tenant_id=made["tid"]).delete()
        db.query(Tenant).filter_by(id=made["tid"]).delete(); db.commit()


def _auth(s): return {"Authorization": f"Bearer {s}"}


def test_domain_key_cannot_create_domain(app_client, keys):
    r = app_client.post("/v1/domains", headers=_auth(keys["domain"]),
                        json={"domain": "nope.test"})
    assert r.status_code == 403


def test_domain_key_cannot_read_quarantine(app_client, keys):
    assert app_client.get("/v1/quarantine", headers=_auth(keys["domain"])).status_code == 403


def test_platform_key_creates_domain(app_client, keys):
    d = f"created-{uuid.uuid4().hex[:6]}.test"
    r = app_client.post("/v1/domains", headers=_auth(keys["platform"]), json={"domain": d})
    assert r.status_code == 201 and r.json()["domain"] == d
    created_id = r.json()["id"]
    with get_session() as db:
        db.query(TenantMatchingRule).filter_by(tenant_id=created_id).delete()
        db.query(Tenant).filter_by(id=created_id).delete(); db.commit()


def test_platform_key_duplicate_domain_409(app_client, keys):
    with get_session() as db:
        dom = db.query(Tenant).filter_by(id=keys["tid"]).one().domain
    r = app_client.post("/v1/domains", headers=_auth(keys["platform"]), json={"domain": dom})
    assert r.status_code == 409


def test_platform_key_reads_quarantine(app_client, keys):
    assert app_client.get("/v1/quarantine", headers=_auth(keys["platform"])).status_code == 200
```

- [ ] **Step 2: Lancer — échoue** (404 sur les routes absentes)

Run: `MSYS_NO_PATHCONV=1 docker compose -f infra/docker-compose.yml run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest tests/test_public_write.py -q`

- [ ] **Step 3: Ajouter les routes**

Dans `backend/app/api/public.py`, compléter les imports puis ajouter les routes :
```python
from pydantic import BaseModel, field_validator

from app.api.admin import TenantIn          # réutilise la validation de domaine
from app.db.models import Email
from app.db.session import tenant_scoped_session
from app.services.audit import audit
from app.services.tenants import ensure_tenant
```
```python
@router.get("/quarantine", dependencies=[Depends(require_platform)])
def quarantine():
    """Rapports non attribués (tenant_id NULL, needs_review). Cross-tenant → plateforme."""
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        rows = (db.query(Email)
                  .filter(Email.tenant_id.is_(None), Email.status == "needs_review")
                  .order_by(Email.received_at.desc()).limit(500).all())
        return [{"id": str(e.id), "message_id": e.message_id, "from_address": e.from_address,
                 "subject": e.subject,
                 "received_at": e.received_at.isoformat() if e.received_at else None}
                for e in rows]


@router.post("/domains", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_platform)])
def create_domain(body: TenantIn, ctx=Depends(get_tenant_ctx)):
    """Crée un domaine (= un tenant). Même logique que POST /admin/tenants."""
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        if db.query(Tenant).filter_by(domain=body.domain).first():
            raise HTTPException(status.HTTP_409_CONFLICT, "Ce domaine est déjà surveillé")
        tenant, _ = ensure_tenant(db, body.domain, body.name)
        out = {"id": str(tenant.id), "domain": tenant.domain, "name": tenant.name}
        db.commit()
    audit(actor=ctx.user, action="tenant.created", target_id=out["id"],
          metadata={"domain": out["domain"], "via": "api_v1"})
    return out
```

- [ ] **Step 4: Lancer — passe**

Run: (même commande qu'au Step 2) → PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/app/api/public.py backend/tests/test_public_write.py
git commit -m "feat(api): /api/v1 quarantaine + creation de domaine (cle plateforme)"
```

---

### Task 5 : Gestion des clés — routes admin JWT

**Files:**
- Create: `backend/app/api/api_keys_admin.py`
- Modify: `backend/app/main.py` (enregistrer le routeur)
- Test: `backend/tests/test_admin_api_keys.py`

**Interfaces:**
- Consumes: `ApiKey`, `Tenant`, `generate_key`, `require_role`, `get_tenant_ctx`, `audit`, `tenant_scoped_session`.
- Produces: `POST /admin/api-keys`, `GET /admin/api-keys`, `DELETE /admin/api-keys/{id}`.

- [ ] **Step 1: Écrire le test (échoue)**

`backend/tests/test_admin_api_keys.py` :
```python
"""Gestion des clés API par un admin : secret rendu une seule fois, jamais relisté."""
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.api_keys_admin import router
from app.auth.middleware import TenantContext
from app.db.models import ApiKey, Tenant
from app.db.session import get_session

ADMIN = "admin-keys@test"


@pytest.fixture
def client():
    app = FastAPI()
    ctx = TenantContext(user=ADMIN, role="platform_admin", tenant_ids=(),
                        active_tenant=None, bypass=True)

    @app.middleware("http")
    async def inject(request, call_next):
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def a_tenant():
    with get_session() as db:
        t = Tenant(domain=f"ak-{uuid.uuid4().hex[:6]}.test", name="AK"); db.add(t); db.commit()
        tid = str(t.id)
    yield tid
    with get_session() as db:
        db.query(ApiKey).filter_by(tenant_id=tid).delete()
        db.query(Tenant).filter_by(id=tid).delete(); db.commit()


def _cleanup(kid):
    with get_session() as db:
        db.query(ApiKey).filter_by(id=kid).delete(); db.commit()


def test_create_platform_key_returns_secret_once(client):
    r = client.post("/admin/api-keys", json={"scope": "platform", "label": "etl"})
    assert r.status_code == 201
    body = r.json()
    assert body["secret"].startswith("sk_plat_") and body["scope"] == "platform"
    _cleanup(body["id"])


def test_create_domain_key_requires_tenant(client):
    assert client.post("/admin/api-keys", json={"scope": "domain", "label": "x"}).status_code == 400


def test_create_domain_key(client, a_tenant):
    r = client.post("/admin/api-keys",
                    json={"scope": "domain", "tenant_id": a_tenant, "label": "client"})
    assert r.status_code == 201 and r.json()["secret"].startswith("sk_dom_")


def test_list_never_returns_secret(client):
    r = client.post("/admin/api-keys", json={"scope": "platform", "label": "l"})
    kid = r.json()["id"]
    lst = client.get("/admin/api-keys").json()
    row = next(k for k in lst if k["id"] == kid)
    assert "secret" not in row and "key_hash" not in row and row["prefix"].startswith("sk_plat_")
    _cleanup(kid)


def test_revoke_is_idempotent(client):
    kid = client.post("/admin/api-keys", json={"scope": "platform", "label": "l"}).json()["id"]
    assert client.delete(f"/admin/api-keys/{kid}").status_code == 204
    assert client.delete(f"/admin/api-keys/{kid}").status_code == 204  # idempotent
    row = next(k for k in client.get("/admin/api-keys").json() if k["id"] == kid)
    assert row["revoked_at"] is not None
    _cleanup(kid)
```

- [ ] **Step 2: Lancer — échoue** (`ModuleNotFoundError`)

Run: `MSYS_NO_PATHCONV=1 docker compose -f infra/docker-compose.yml run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest tests/test_admin_api_keys.py -q`

- [ ] **Step 3: Écrire le routeur admin**

`backend/app/api/api_keys_admin.py` :
```python
"""Gestion des clés API (admin JWT uniquement — hors /api/v1, donc inatteignable par une clé)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth.deps import get_tenant_ctx, require_role
from app.db.models import ApiKey, Tenant
from app.db.session import tenant_scoped_session
from app.services.api_keys import generate_key
from app.services.audit import audit

router = APIRouter(prefix="/admin", tags=["admin"],
                   dependencies=[Depends(require_role("platform_admin"))])


class ApiKeyIn(BaseModel):
    scope: str                     # 'platform' | 'domain'
    tenant_id: str | None = None
    label: str = ""


def _row(k: ApiKey, domain: str | None) -> dict:
    return {"id": str(k.id), "scope": k.scope,
            "tenant_id": str(k.tenant_id) if k.tenant_id else None, "domain": domain,
            "prefix": k.prefix, "label": k.label,
            "created_at": k.created_at.isoformat() if k.created_at else None,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "revoked_at": k.revoked_at.isoformat() if k.revoked_at else None}


@router.post("/api-keys", status_code=status.HTTP_201_CREATED)
def create_api_key(body: ApiKeyIn, ctx=Depends(get_tenant_ctx)):
    if body.scope not in ("platform", "domain"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "scope invalide")
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        tenant = None
        if body.scope == "domain":
            if not body.tenant_id:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "tenant_id requis (scope domaine)")
            tenant = db.get(Tenant, body.tenant_id)
            if not tenant:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "domaine introuvable")
        elif body.tenant_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "tenant_id interdit (scope plateforme)")

        secret, prefix, key_hash = generate_key(body.scope)
        key = ApiKey(scope=body.scope, tenant_id=(tenant.id if tenant else None),
                     prefix=prefix, key_hash=key_hash, label=body.label or body.scope,
                     created_by=ctx.user)
        db.add(key); db.flush()
        out = _row(key, tenant.domain if tenant else None)
        db.commit()
    audit(actor=ctx.user, action="api_key.created", target_id=out["id"],
          metadata={"scope": out["scope"], "domain": out["domain"]})
    # Le secret n'apparaît QUE dans cette réponse de création — jamais relisté ensuite.
    return {**out, "secret": secret}


@router.get("/api-keys")
def list_api_keys():
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        domains = dict(db.query(Tenant.id, Tenant.domain).all())
        return [_row(k, domains.get(k.tenant_id))
                for k in db.query(ApiKey).order_by(ApiKey.created_at.desc()).all()]


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(key_id: str, ctx=Depends(get_tenant_ctx)):
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        key = db.get(ApiKey, key_id)
        if key and key.revoked_at is None:      # idempotent : déjà révoquée → no-op
            key.revoked_at = datetime.now(timezone.utc)
            db.commit()
            audit(actor=ctx.user, action="api_key.revoked", target_id=key_id)
```

- [ ] **Step 4: Enregistrer le routeur**

Dans `backend/app/main.py` :
```python
from app.api import api_keys_admin
...
app.include_router(api_keys_admin.router)
```

- [ ] **Step 5: Lancer — passe**

Run: (même commande qu'au Step 2) → PASS.

- [ ] **Step 6: Commit**
```bash
git add backend/app/api/api_keys_admin.py backend/app/main.py backend/tests/test_admin_api_keys.py
git commit -m "feat(api): routes admin de gestion des cles API (creer/lister/revoquer)"
```

---

### Task 6 : Extension du test d'isolation cross-tenant (invariant #7)

**Files:**
- Modify: `backend/tests/test_tenant_isolation.py` (ajouter les cas clé API)

**Interfaces:**
- Consumes: `TenantMiddleware`, `public.router`, `ApiKey`, `api_keys.generate_key`, fixtures locales.

- [ ] **Step 1: Ajouter les cas d'isolation clé API**

Append à `backend/tests/test_tenant_isolation.py` :
```python
# --------------------------------------------------------------- Isolation clés API
import uuid as _uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import public
from app.auth.middleware import TenantMiddleware
from app.db.models import ApiKey
from app.services import api_keys


def _api_client():
    app = FastAPI(); app.add_middleware(TenantMiddleware); app.include_router(public.router)
    return TestClient(app)


def _domain_key_for(tid: str) -> str:
    secret, prefix, h = api_keys.generate_key("domain")
    with get_session() as db:
        db.add(ApiKey(scope="domain", tenant_id=tid, prefix=prefix, key_hash=h,
                      label="iso", created_by="iso@test"))
        db.commit()
    return secret


def test_api_key_domain_a_ne_voit_que_a(seed_two_tenants):
    tid_a, tid_b = seed_two_tenants
    secret = _domain_key_for(tid_a)
    try:
        client = _api_client()
        r = client.get("/v1/domains", headers={"Authorization": f"Bearer {secret}"})
        assert r.status_code == 200
        ids = {d["id"] for d in r.json()}
        assert ids == {tid_a}                 # jamais B
    finally:
        with get_session() as db:
            db.query(ApiKey).filter_by(key_hash=api_keys.hash_secret(secret)).delete(); db.commit()


def test_api_key_domain_ne_peut_pas_creer_ni_lire_quarantaine(seed_two_tenants):
    tid_a, _ = seed_two_tenants
    secret = _domain_key_for(tid_a)
    try:
        client = _api_client()
        h = {"Authorization": f"Bearer {secret}"}
        assert client.post("/v1/domains", headers=h, json={"domain": "x.test"}).status_code == 403
        assert client.get("/v1/quarantine", headers=h).status_code == 403
    finally:
        with get_session() as db:
            db.query(ApiKey).filter_by(key_hash=api_keys.hash_secret(secret)).delete(); db.commit()


def test_api_key_ne_peut_pas_toucher_admin(seed_two_tenants):
    tid_a, _ = seed_two_tenants
    secret = _domain_key_for(tid_a)
    try:
        # Monte le routeur admin DERRIÈRE le vrai middleware : une clé sk_ hors /api/v1 → 403.
        from app.api.admin import router as admin_router
        app = FastAPI(); app.add_middleware(TenantMiddleware); app.include_router(admin_router)
        r = TestClient(app).get("/admin/tenants", headers={"Authorization": f"Bearer {secret}"})
        assert r.status_code == 403
    finally:
        with get_session() as db:
            db.query(ApiKey).filter_by(key_hash=api_keys.hash_secret(secret)).delete(); db.commit()
```

- [ ] **Step 2: Lancer le test d'isolation complet — passe**

Run: `MSYS_NO_PATHCONV=1 docker compose -f infra/docker-compose.yml run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest tests/test_tenant_isolation.py -v`
Expected: tous verts (anciens + 3 nouveaux).

- [ ] **Step 3: Suite complète + lint**

Run: `MSYS_NO_PATHCONV=1 docker compose -f infra/docker-compose.yml run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest -q`
Run: `MSYS_NO_PATHCONV=1 docker compose -f infra/docker-compose.yml run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api ruff check app scripts tests`
Expected: PASS + « All checks passed! ».

- [ ] **Step 4: Commit**
```bash
git add backend/tests/test_tenant_isolation.py
git commit -m "test(iso): isolation cross-tenant des cles API (domaine != autre, pas d'admin)"
```

---

### Task 7 : UI — section « Clés API » dans Paramètres

**Files:**
- Create: `frontend/src/api/apiKeys.ts`
- Modify: `frontend/src/pages/Settings.tsx` (ajouter la section)
- Verify: `tsc -b` + `vite build`

**Interfaces:**
- Consumes: `api` (client), `useTenants` (déjà présent), routes `/admin/api-keys`.
- Produces: hooks `useApiKeys`, `useCreateApiKey`, `useRevokeApiKey` ; composant `ApiKeysSection`.

- [ ] **Step 1: Écrire les hooks**

`frontend/src/api/apiKeys.ts` :
```typescript
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";

export interface ApiKey {
  id: string;
  scope: "platform" | "domain";
  tenant_id: string | null;
  domain: string | null;
  prefix: string;
  label: string;
  created_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface CreatedApiKey extends ApiKey {
  secret: string; // rendu une seule fois, à la création
}

export const useApiKeys = () =>
  useQuery({ queryKey: ["api-keys"], queryFn: () => api<ApiKey[]>("/admin/api-keys") });

export function useCreateApiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { scope: string; tenant_id?: string; label: string }) =>
      api<CreatedApiKey>("/admin/api-keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(b),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["api-keys"] }),
  });
}

export function useRevokeApiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/admin/api-keys/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["api-keys"] }),
  });
}
```

- [ ] **Step 2: Ajouter la section dans Settings**

Dans `frontend/src/pages/Settings.tsx` :

(a) imports :
```typescript
import { useApiKeys, useCreateApiKey, useRevokeApiKey, type CreatedApiKey } from "../api/apiKeys";
```

(b) rendre `<ApiKeysSection />` en fin du conteneur racine, juste avant la fermeture `</div>` du `space-y-6 p-6` (après le bloc `{creating && …}`) :
```tsx
      <ApiKeysSection tenants={tenants.data ?? []} />
```

(c) ajouter le composant en bas du fichier :
```tsx
function ApiKeysSection({ tenants }: { tenants: { id: string; domain: string }[] }) {
  const keys = useApiKeys();
  const create = useCreateApiKey();
  const revoke = useRevokeApiKey();
  const [scope, setScope] = useState("platform");
  const [tenantId, setTenantId] = useState("");
  const [label, setLabel] = useState("");
  const [fresh, setFresh] = useState<CreatedApiKey | null>(null);
  const [error, setError] = useState("");

  const ready = label.trim() !== "" && (scope === "platform" || tenantId !== "");

  async function submit() {
    setError("");
    try {
      const created = await create.mutateAsync({
        scope,
        tenant_id: scope === "domain" ? tenantId : undefined,
        label: label.trim(),
      });
      setFresh(created);
      setLabel("");
      setTenantId("");
    } catch {
      setError("Création impossible.");
    }
  }

  return (
    <section className="space-y-3 rounded border bg-white p-4">
      <div>
        <h2 className="text-sm font-medium text-gray-700">Clés API</h2>
        <p className="mt-1 max-w-2xl text-sm text-gray-500">
          Pour les programmes tiers. Une clé <strong>plateforme</strong> lit tous les domaines
          et peut en créer ; une clé <strong>par-domaine</strong> ne lit que son domaine.
          Le secret ne s'affiche qu'une seule fois.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-2">
        <label className="text-sm">
          <span className="mr-1 text-xs text-gray-600">Type</span>
          <select value={scope} onChange={(e) => setScope(e.target.value)}
                  className="rounded border px-2 py-1.5 text-sm">
            <option value="platform">Plateforme</option>
            <option value="domain">Par-domaine</option>
          </select>
        </label>
        {scope === "domain" && (
          <select value={tenantId} onChange={(e) => setTenantId(e.target.value)}
                  className="rounded border px-2 py-1.5 text-sm">
            <option value="">— domaine —</option>
            {tenants.map((t) => <option key={t.id} value={t.id}>{t.domain}</option>)}
          </select>
        )}
        <input value={label} onChange={(e) => setLabel(e.target.value)}
               placeholder="Libellé (ex. ETL client X)"
               className="rounded border px-3 py-1.5 text-sm" />
        <button onClick={submit} disabled={!ready || create.isPending}
                className="rounded bg-gray-900 px-3 py-1.5 text-sm text-white disabled:opacity-40">
          Créer une clé
        </button>
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}

      {fresh && (
        <div className="rounded border bg-amber-50 p-3">
          <div className="text-sm">
            Copiez ce secret maintenant — il ne sera <strong>plus jamais</strong> affiché :
          </div>
          <div className="mt-1 flex items-center gap-3">
            <code className="rounded border bg-white px-2 py-1 font-mono text-xs break-all">
              {fresh.secret}
            </code>
            <button onClick={() => navigator.clipboard.writeText(fresh.secret)}
                    className="text-xs text-gray-600 hover:underline">Copier</button>
            <button onClick={() => setFresh(null)}
                    className="text-xs text-gray-600 hover:underline">J'ai noté</button>
          </div>
        </div>
      )}

      <div className="overflow-x-auto rounded border">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-gray-500">
              <th className="px-3 py-2 font-medium">Clé</th>
              <th className="px-3 py-2 font-medium">Portée</th>
              <th className="px-3 py-2 font-medium">Dernière utilisation</th>
              <th className="px-3 py-2 font-medium">État</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {(keys.data ?? []).map((k) => (
              <tr key={k.id} className="border-t">
                <td className="px-3 py-2">
                  <div className="font-mono text-xs">{k.prefix}…</div>
                  <div className="text-xs text-gray-500">{k.label}</div>
                </td>
                <td className="px-3 py-2">{k.scope === "platform" ? "Plateforme" : k.domain}</td>
                <td className="px-3 py-2 text-gray-500">
                  {k.last_used_at ? new Date(k.last_used_at).toLocaleString("fr-FR") : "jamais"}
                </td>
                <td className="px-3 py-2">
                  {k.revoked_at
                    ? <span className="rounded bg-gray-200 px-1.5 py-0.5 text-xs text-gray-700">Révoquée</span>
                    : <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-xs text-emerald-800">Active</span>}
                </td>
                <td className="px-3 py-2 text-right">
                  {!k.revoked_at && (
                    <button onClick={() => revoke.mutate(k.id)}
                            className="text-xs text-red-600 hover:underline">Révoquer</button>
                  )}
                </td>
              </tr>
            ))}
            {keys.isSuccess && keys.data!.length === 0 && (
              <tr><td colSpan={5} className="px-3 py-4 text-center text-gray-500">Aucune clé.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
```

- [ ] **Step 3: Vérifier le build**

Run: `cd frontend && node node_modules/typescript/bin/tsc -b && node node_modules/vite/bin/vite.js build`
Expected: exit 0 pour les deux (aucune erreur de type).

- [ ] **Step 4: Commit**
```bash
git add frontend/src/api/apiKeys.ts frontend/src/pages/Settings.tsx
git commit -m "feat(front): section Cles API dans Parametres (creer/copier-une-fois/revoquer)"
```

---

## Notes d'exécution

- **Commandes backend** : l'image `api` embarque le code ; pour tester du code non commité,
  utiliser le montage `-v "D:/code/dmarc/backend:/app"` (voir chaque Step). Le préfixe
  `MSYS_NO_PATHCONV=1` est obligatoire (chemins Windows→conteneur). Les migrations exigent
  `-e DATABASE_URL_MIGRATE=postgresql://postgres:postgres@postgres:5432/reports`.
- **Ordre** : 1 → 2 → 3 → 4 → 5 → 6 → 7 (dépendances strictes). Ne pas paralléliser.
- **Vérification finale (avant fusion)** : `pytest` complet vert, `ruff check app scripts tests`
  vert, `test_tenant_isolation.py` vert (bloquant), `tsc -b` + `vite build` verts.
- **Contrôle réel post-déploiement** : créer une clé plateforme dans Paramètres →
  `curl -H "Authorization: Bearer sk_plat_…" https://dmarc-reports.lsiparis.tech/api/v1/domains`
  et un `POST /api/v1/domains` ; puis une clé domaine → vérifier qu'un `GET /api/v1/reports`
  ne renvoie que son domaine et qu'un `POST /api/v1/domains` est refusé (403).
```

Auto-vérification du plan (couverture spec, placeholders, cohérence de types) faite ; RAS.
