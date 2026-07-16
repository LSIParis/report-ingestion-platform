# Fiche de détail utilisateur — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Associer à chaque compte une fiche d'identité (nom, prénom, société, adresse, téléphone, e-mail), éditable par l'admin (tout compte) et par chacun pour soi (« Mon profil »).

**Architecture:** Cinq colonnes nullables sur `app_user` ; un `PATCH /auth/me` (identité de soi, jamais rôle/domaines) et l'extension de `PATCH /admin/users/{id}` (identité + e-mail d'un tiers), tous deux avec unicité e-mail ; côté front, un modal `ProfileDialog` ouvert depuis « Mon profil » (menu compte) et depuis une action « Fiche » sur la page Paramètres.

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy 2.0 · Alembic · Pydantic · pytest · React 19 + TypeScript · Vite · TanStack Query · Tailwind.

**Spec:** `docs/superpowers/specs/2026-07-15-fiche-utilisateur-design.md`

## Global Constraints

- **Isolation multitenant (CLAUDE.md)** : `app_user` n'est **pas** une table métier tenant — pas de `tenant_id`, pas de RLS. Les routes users passent par `tenant_scoped_session(tenant_id=None, bypass=True)` (déjà le cas). `PATCH /auth/me` ne modifie **que** le compte porteur du jeton (résolu par `ctx.user`) et **jamais** `role`/`tenant_ids` (absents du schéma). `tests/test_tenant_isolation.py` reste vert (inchangé).
- **E-mail** : identifiant de connexion, **requis**, mis en **minuscules** et validé (`@`, pas d'espace) ; **unique** → **409** si déjà pris par un autre compte. Une seule fonction `normalize_email` sert de source unique (réutilisée par les schémas admin et « moi »).
- **Champs profil** (`first_name`, `last_name`, `company`, `address`, `phone`) : **optionnels**, `Text` nullable ; chaîne vide → `NULL`.
- **Changement de son propre e-mail** (via `PATCH /auth/me`) : le jeton porte l'ancien e-mail (`sub`) ; après succès, le front **efface la session et redirige `/login`** (reconnexion). Un admin changeant l'e-mail d'un tiers : aucun impact sur sa session.
- Commentaires et libellés en français. Messages de commit en français **sans accents**.
- **Back-end** : le conteneur `api` bake le code → tester du code non commité via un **run monté** depuis `infra/` (Git Bash, `MSYS_NO_PATHCONV=1` INDISPENSABLE) :
  ```bash
  MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest <chemin> -q
  ```
  Le lint CI couvre `app scripts tests` → lancer `... api ruff check app scripts tests`. **Ne jamais** écrire de point-virgule multi-instructions (`a; b`) — E702 rejeté.
- **Front-end** : pas de harnais de test. Vérification = `tsc -b` + `vite build` verts :
  ```bash
  MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/frontend:/app" -w /app frontend sh -c "npm install --silent && npx tsc -b && npx vite build"
  ```

## Structure des fichiers

| Fichier | Rôle |
|---|---|
| `backend/app/db/models.py` | **Modifier.** 5 colonnes sur `AppUser`. |
| `backend/migrations/versions/0011_user_profile.py` | **Créer.** Ajoute les colonnes (nullable). |
| `backend/app/auth/emails.py` | **Créer.** `normalize_email(v) -> str` (source unique). |
| `backend/app/auth/login.py` | **Modifier.** `MeOut` + `me()` enrichis ; `PATCH /auth/me`. |
| `backend/app/api/admin.py` | **Modifier.** `UserOut`/`_serialize`/`UserPatch`/`update_user` étendus ; `UserIn` réutilise `normalize_email`. |
| `backend/tests/test_profile_me.py` | **Créer.** `GET`/`PATCH /auth/me`. |
| `backend/tests/test_admin_user_profile.py` | **Créer.** `PATCH /admin/users/{id}` identité + e-mail. |
| `frontend/src/api/account.ts` | **Modifier.** `Me` + `useUpdateProfile`. |
| `frontend/src/api/users.ts` | **Modifier.** `User` + `useUpdateUser` étendu. |
| `frontend/src/components/ProfileDialog.tsx` | **Créer.** Le modal fiche (self/admin). |
| `frontend/src/components/AccountMenu.tsx` | **Modifier.** Entrée « Mon profil ». |
| `frontend/src/pages/Settings.tsx` | **Modifier.** Action « Fiche » par ligne. |

Ordre : 1 (colonnes+migration) → 2 (emails.py + `/auth/me`) → 3 (admin) → 4 (api front) → 5 (ProfileDialog + branchements).

---

### Task 1: Colonnes `app_user` + migration `0011`

**Files:**
- Modify: `backend/app/db/models.py` (classe `AppUser`, ~lignes 42-48)
- Create: `backend/migrations/versions/0011_user_profile.py`

**Interfaces:**
- Produces: cinq colonnes nullables sur `app_user` : `first_name`, `last_name`, `company`, `address`, `phone` (toutes `Text`).

- [ ] **Step 1: Ajouter les colonnes au modèle**

Dans `backend/app/db/models.py`, classe `AppUser`, juste après `created_at` (fin de classe) :

```python
    first_name: Mapped[str | None] = mapped_column(Text)
    last_name: Mapped[str | None] = mapped_column(Text)
    company: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
```

- [ ] **Step 2: Écrire la migration**

Créer `backend/migrations/versions/0011_user_profile.py` :

```python
"""fiche d'identite sur app_user (nom, prenom, societe, adresse, telephone)

Revision ID: 0011_user_profile
Revises: 0010_report_summary
"""
import sqlalchemy as sa
from alembic import op

revision = "0011_user_profile"
down_revision = "0010_report_summary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for col in ("first_name", "last_name", "company", "address", "phone"):
        op.add_column("app_user", sa.Column(col, sa.Text(), nullable=True))


def downgrade() -> None:
    for col in ("phone", "address", "company", "last_name", "first_name"):
        op.drop_column("app_user", col)
```

- [ ] **Step 3: Appliquer et vérifier**

```bash
MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app -e DATABASE_URL_MIGRATE=postgresql://postgres:postgres@postgres:5432/reports api alembic upgrade head
```
Expected : `0011_user_profile` s'applique sans erreur. Contrôle :
```bash
docker compose -f infra/docker-compose.yml exec -T postgres psql -U postgres -d reports -c "\d app_user" | grep -E "first_name|phone"
```
Expected : les colonnes apparaissent.

- [ ] **Step 4: Commit**

```bash
git add backend/app/db/models.py backend/migrations/versions/0011_user_profile.py
git commit -m "feat(auth): colonnes d identite sur app_user + migration 0011

first_name, last_name, company, address, phone -- nullables, aucun backfill (NULL = non
renseigne). app_user n est pas une table tenant : aucune RLS."
```

---

### Task 2: `normalize_email` + `GET`/`PATCH /auth/me`

**Files:**
- Create: `backend/app/auth/emails.py`
- Modify: `backend/app/auth/login.py`
- Test: `backend/tests/test_profile_me.py`

**Interfaces:**
- Consumes: colonnes `AppUser` (Task 1).
- Produces:
  - `normalize_email(v: str) -> str` (strip+lower, lève `ValueError` si invalide).
  - `MeOut` gagne `first_name`/`last_name`/`company`/`address`/`phone` (`str | None`).
  - `GET /auth/me` les renvoie ; `PATCH /auth/me` (body `ProfileIn`) met à jour l'identité de l'appelant, e-mail unique (409), renvoie **204**.

- [ ] **Step 1: Écrire les tests d'abord**

Créer `backend/tests/test_profile_me.py` :

```python
"""GET / PATCH /auth/me : la fiche de l'utilisateur connecte.

PATCH /me ne doit toucher QUE l'appelant (resolu par ctx.user) et JAMAIS role/domaines.
"""
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.login import router
from app.auth.middleware import TenantContext
from app.auth.passwords import hash_password
from app.db.models import AppUser
from app.db.session import get_session


def _client(email):
    app = FastAPI()
    ctx = TenantContext(user=email, role="tenant_viewer", tenant_ids=(),
                        active_tenant=None, bypass=False)

    @app.middleware("http")
    async def inject_ctx(request, call_next):
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def _make_user(email):
    with get_session() as db:
        u = AppUser(email=email, role="tenant_viewer", password_hash=hash_password("x" * 12))
        db.add(u)
        db.flush()
        uid = str(u.id)
        db.commit()
    return uid


def _cleanup(*emails):
    with get_session() as db:
        for e in emails:
            db.query(AppUser).filter_by(email=e).delete()
        db.commit()


def test_get_me_renvoie_les_champs_profil():
    email = f"me-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(email)
    try:
        b = _client(email).get("/auth/me").json()
        assert b["email"] == email
        assert b["first_name"] is None
        assert "phone" in b
    finally:
        _cleanup(email)


def test_patch_me_met_a_jour_l_identite():
    email = f"me-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(email)
    try:
        c = _client(email)
        r = c.patch("/auth/me", json={"email": email, "first_name": "Ada",
                                      "last_name": "Lovelace", "company": "LSI",
                                      "address": "1 rue X", "phone": "0600000000"})
        assert r.status_code == 204
        b = c.get("/auth/me").json()
        assert b["first_name"] == "Ada"
        assert b["company"] == "LSI"
        assert b["phone"] == "0600000000"
    finally:
        _cleanup(email)


def test_patch_me_ignore_role_et_domaines():
    # role/tenant_ids ne sont pas dans le schema -> ignores par FastAPI, le role ne bouge pas.
    email = f"me-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(email)
    try:
        c = _client(email)
        c.patch("/auth/me", json={"email": email, "role": "platform_admin",
                                  "tenant_ids": []})
        with get_session() as db:
            u = db.query(AppUser).filter_by(email=email).first()
            assert u.role == "tenant_viewer"
    finally:
        _cleanup(email)


def test_patch_me_email_deja_pris_409():
    a = f"a-{uuid.uuid4().hex[:8]}@test.fr"
    b_email = f"b-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(a)
    _make_user(b_email)
    try:
        r = _client(a).patch("/auth/me", json={"email": b_email})
        assert r.status_code == 409
    finally:
        _cleanup(a, b_email)


def test_patch_me_email_mis_en_minuscules():
    email = f"me-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(email)
    new = email.upper()
    try:
        r = _client(email).patch("/auth/me", json={"email": new})
        assert r.status_code == 204
        with get_session() as db:
            assert db.query(AppUser).filter_by(email=email).first() is not None
    finally:
        _cleanup(email)
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest tests/test_profile_me.py -q`
Expected: FAIL (`/auth/me` PATCH inexistant, champs profil absents de `MeOut`).

- [ ] **Step 3: Créer `normalize_email`**

Créer `backend/app/auth/emails.py` :

```python
"""Normalisation/validation de l'adresse e-mail, source UNIQUE.

L'adresse n'est qu'un identifiant de connexion : on la met en minuscules et on refuse
l'evidemment invalide, sans embarquer un validateur RFC 5322 complet. Partagee par les
schemas admin (UserIn/UserPatch) et « moi » (ProfileIn) pour ne pas diverger.
"""
from __future__ import annotations


def normalize_email(v: str) -> str:
    v = v.strip().lower()
    if "@" not in v or v.startswith("@") or v.endswith("@") or " " in v:
        raise ValueError("adresse e-mail invalide")
    return v
```

- [ ] **Step 4: Étendre `MeOut`, `me()`, et ajouter `PATCH /auth/me`**

Dans `backend/app/auth/login.py` :

Ajouter aux imports (avec les autres `from app.`) et pydantic :

```python
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth.emails import normalize_email
```

(`BaseModel, ConfigDict, Field` sont déjà importés — ajouter `field_validator`.)

Remplacer `MeOut` par :

```python
class MeOut(BaseModel):
    email: str
    role: str
    tenants: list[TenantOut]
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None
    address: str | None = None
    phone: str | None = None
```

Remplacer le corps de `me()` (après le docstring) par :

```python
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        user = db.query(AppUser).filter_by(email=ctx.user).first()
        q = db.query(Tenant)
        if ctx.role != "platform_admin":
            q = q.filter(Tenant.id.in_(ctx.tenant_ids))
        tenants = q.order_by(Tenant.name).all()
        return MeOut(
            email=ctx.user, role=ctx.role,
            tenants=[TenantOut.model_validate(t) for t in tenants],
            first_name=user.first_name if user else None,
            last_name=user.last_name if user else None,
            company=user.company if user else None,
            address=user.address if user else None,
            phone=user.phone if user else None,
        )
```

Ajouter, après l'endpoint `me` (avant `class PasswordIn`) :

```python
class ProfileIn(BaseModel):
    email: str
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None
    address: str | None = None
    phone: str | None = None

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return normalize_email(v)


@router.patch("/me", status_code=status.HTTP_204_NO_CONTENT)
def update_me(body: ProfileIn, ctx=Depends(get_tenant_ctx)):
    """Mise a jour par l'utilisateur de SA PROPRE fiche d'identite.

    Ne touche JAMAIS role ni domaines (absents de ProfileIn) : pas d'elevation de
    privilege possible. Le compte est resolu par ctx.user (e-mail du jeton signe).
    """
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        user = db.query(AppUser).filter_by(email=ctx.user).first()
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Compte introuvable")
        if body.email != user.email and db.query(AppUser).filter(
                AppUser.email == body.email, AppUser.id != user.id).first():
            raise HTTPException(status.HTTP_409_CONFLICT, "Cet e-mail est deja utilise")
        user.email = body.email
        user.first_name = body.first_name or None
        user.last_name = body.last_name or None
        user.company = body.company or None
        user.address = body.address or None
        user.phone = body.phone or None
        db.commit()

    audit(actor=ctx.user, action="user.profile_updated", tenant_id=ctx.active_tenant)
```

- [ ] **Step 5: Lancer, vérifier que ça passe**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest tests/test_profile_me.py -q`
Expected: PASS (5 tests).

- [ ] **Step 6: Lint + commit**

```bash
... api ruff check app/auth/emails.py app/auth/login.py tests/test_profile_me.py
git add backend/app/auth/emails.py backend/app/auth/login.py backend/tests/test_profile_me.py
git commit -m "feat(auth): fiche de soi via GET/PATCH /auth/me (identite, e-mail unique)

PATCH /me ne modifie que l identite de l appelant, jamais role ni domaines. E-mail mis en
minuscules et unique (409). normalize_email() partage. GET /me renvoie les champs profil."
```

---

### Task 3: `PATCH /admin/users/{id}` — identité + e-mail

**Files:**
- Modify: `backend/app/api/admin.py`
- Test: `backend/tests/test_admin_user_profile.py`

**Interfaces:**
- Consumes: colonnes `AppUser` (Task 1), `normalize_email` (Task 2).
- Produces: `UserOut` gagne les 5 champs ; `PATCH /admin/users/{id}` accepte `email` + les 5 champs (unicité e-mail 409), en plus de `role`/`tenant_ids`.

- [ ] **Step 1: Écrire le test d'abord**

Créer `backend/tests/test_admin_user_profile.py` :

```python
"""PATCH /admin/users/{id} : l'admin edite l'identite + l'e-mail d'un compte tiers."""
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import router
from app.auth.middleware import TenantContext
from app.auth.passwords import hash_password
from app.db.models import AppUser
from app.db.session import get_session


def _admin_client(admin_email):
    app = FastAPI()
    ctx = TenantContext(user=admin_email, role="platform_admin", tenant_ids=(),
                        active_tenant=None, bypass=True)

    @app.middleware("http")
    async def inject_ctx(request, call_next):
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def _make_user(email):
    with get_session() as db:
        u = AppUser(email=email, role="tenant_viewer", password_hash=hash_password("x" * 12))
        db.add(u)
        db.flush()
        uid = str(u.id)
        db.commit()
    return uid


def _cleanup(*emails):
    with get_session() as db:
        for e in emails:
            db.query(AppUser).filter_by(email=e).delete()
        db.commit()


def test_admin_met_a_jour_identite_et_email():
    admin = f"admin-{uuid.uuid4().hex[:8]}@test.fr"
    target = f"t-{uuid.uuid4().hex[:8]}@test.fr"
    new_email = f"t2-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(admin)
    uid = _make_user(target)
    try:
        r = _admin_client(admin).patch(f"/admin/users/{uid}", json={
            "email": new_email, "first_name": "Grace", "company": "LSI"})
        assert r.status_code == 200
        body = r.json()
        assert body["email"] == new_email
        assert body["first_name"] == "Grace"
        assert body["company"] == "LSI"
    finally:
        _cleanup(admin, target, new_email)


def test_admin_email_deja_pris_409():
    admin = f"admin-{uuid.uuid4().hex[:8]}@test.fr"
    a = f"a-{uuid.uuid4().hex[:8]}@test.fr"
    b = f"b-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(admin)
    uid_a = _make_user(a)
    _make_user(b)
    try:
        r = _admin_client(admin).patch(f"/admin/users/{uid_a}", json={"email": b})
        assert r.status_code == 409
    finally:
        _cleanup(admin, a, b)
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest tests/test_admin_user_profile.py -q`
Expected: FAIL (`UserPatch` n'a ni `email` ni les champs profil ; `UserOut`/`_serialize` ne les renvoient pas).

- [ ] **Step 3: Étendre schémas + `_serialize` + `update_user`**

Dans `backend/app/api/admin.py` :

Ajouter l'import (avec les autres `from app.`) :

```python
from app.auth.emails import normalize_email
```

Remplacer le validateur d'e-mail de `UserIn` (méthode `_email`) par la version partagée :

```python
    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return normalize_email(v)
```

Étendre `UserOut` (ajouter après `role`) :

```python
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None
    address: str | None = None
    phone: str | None = None
```

Remplacer `UserPatch` par :

```python
class UserPatch(BaseModel):
    role: str | None = None
    tenant_ids: list[UUID] | None = None
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None
    address: str | None = None
    phone: str | None = None

    @field_validator("email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        return normalize_email(v) if v is not None else v
```

Étendre `_serialize` — remplacer son `return` par :

```python
    return {"id": user.id, "email": user.email, "role": user.role,
            "created_at": user.created_at,
            "first_name": user.first_name, "last_name": user.last_name,
            "company": user.company, "address": user.address, "phone": user.phone,
            "tenants": [{"id": str(i), "domain": d} for i, d in rows]}
```

Dans `update_user`, ajouter — **juste avant** `db.flush()` (après le bloc `if body.tenant_ids is not None:`) — l'application de l'e-mail et de l'identité :

```python
        if body.email is not None and body.email != user.email:
            if db.query(AppUser).filter(AppUser.email == body.email,
                                        AppUser.id != user.id).first():
                raise HTTPException(status.HTTP_409_CONFLICT, "Cet e-mail est deja utilise")
            user.email = body.email
        # Identite : une cle presente (meme "") est appliquee ("" -> NULL) ; absente -> intacte.
        # Le formulaire Fiche envoie les cinq champs ; le formulaire role/domaines ne les envoie pas.
        for field in ("first_name", "last_name", "company", "address", "phone"):
            val = getattr(body, field)
            if val is not None:
                setattr(user, field, val or None)
```

- [ ] **Step 4: Lancer les tests (+ existant users)**

```bash
MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest tests/test_admin_user_profile.py tests/test_admin_users.py -q
```
Expected: PASS (les tests existants de `test_admin_users.py` restent verts — `UserPatch`/`_serialize` étendus de façon rétrocompatible).

- [ ] **Step 5: Lint + commit**

```bash
... api ruff check app/api/admin.py tests/test_admin_user_profile.py
git add backend/app/api/admin.py backend/tests/test_admin_user_profile.py
git commit -m "feat(admin): PATCH /admin/users/{id} met a jour l identite + l e-mail

UserOut/_serialize exposent les cinq champs ; UserPatch accepte email (unicite 409) et les
champs profil. UserIn reutilise normalize_email. Retrocompatible avec le formulaire acces."
```

---

### Task 4: Front — types `Me`/`User` + hooks

**Files:**
- Modify: `frontend/src/api/account.ts`, `frontend/src/api/users.ts`

**Interfaces:**
- Consumes: `GET/PATCH /auth/me`, `PATCH /admin/users/{id}` (Tasks 2-3).
- Produces:
  - `Me` gagne `first_name`/`last_name`/`company`/`address`/`phone` (`string | null`).
  - `useUpdateProfile()` → `PATCH /auth/me`.
  - `User` gagne les 5 champs ; `useUpdateUser` accepte `email`/les 5 champs.

- [ ] **Step 1: `account.ts` — `Me` + `useUpdateProfile`**

Dans `frontend/src/api/account.ts` :

Ajouter `useQueryClient` à l'import TanStack :

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
```

Étendre `Me` (après `tenants`) :

```ts
  first_name: string | null;
  last_name: string | null;
  company: string | null;
  address: string | null;
  phone: string | null;
```

Ajouter le hook (après `useChangePassword`) :

```ts
export function useUpdateProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      email: string; first_name: string; last_name: string;
      company: string; address: string; phone: string;
    }) =>
      api<void>("/auth/me", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["me"] }),
  });
}
```

- [ ] **Step 2: `users.ts` — `User` + `useUpdateUser`**

Dans `frontend/src/api/users.ts` :

Étendre l'interface `User` (après `tenants`) :

```ts
  first_name: string | null;
  last_name: string | null;
  company: string | null;
  address: string | null;
  phone: string | null;
```

Élargir le type accepté par `useUpdateUser` (le corps du `mutationFn` reste `PATCH /admin/users/{id}`) :

```ts
    mutationFn: ({ id, ...b }: {
      id: string; role?: string; tenant_ids?: string[]; email?: string;
      first_name?: string; last_name?: string; company?: string;
      address?: string; phone?: string;
    }) =>
      api<User>(`/admin/users/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(b),
      }),
```

(Garder le reste de `useUpdateUser` inchangé — le `useQueryClient`/`onSuccess` d'invalidation existant.)

- [ ] **Step 3: Vérification frontend**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/frontend:/app" -w /app frontend sh -c "npm install --silent && npx tsc -b && npx vite build"`
Expected: `tsc` et `vite build` verts.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/account.ts frontend/src/api/users.ts
git commit -m "feat(front): types Me/User enrichis + useUpdateProfile (PATCH /auth/me)"
```

---

### Task 5: Front — `ProfileDialog` + « Mon profil » + action « Fiche »

**Files:**
- Create: `frontend/src/components/ProfileDialog.tsx`
- Modify: `frontend/src/components/AccountMenu.tsx`, `frontend/src/pages/Settings.tsx`

**Interfaces:**
- Consumes: `useUpdateProfile` (Task 4), `useUpdateUser` (Task 4), `useMe` (`../api/account`), `clearSession` (`../auth/session`), `ApiError` (`../api/client`).
- Produces: rien (feuille).

- [ ] **Step 1: Créer `ProfileDialog.tsx`**

Créer `frontend/src/components/ProfileDialog.tsx` (patron modal de `PasswordDialog` : fond cliquable qui ferme, contenu qui stoppe la propagation) :

```tsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { useUpdateProfile } from "../api/account";
import { ApiError } from "../api/client";
import { useUpdateUser } from "../api/users";
import { clearSession } from "../auth/session";

export interface ProfileValues {
  email: string;
  first_name: string;
  last_name: string;
  company: string;
  address: string;
  phone: string;
}

/** Convertit les `null` de l'API en `""` pour le formulaire. */
export function toProfileValues(u: {
  email: string; first_name: string | null; last_name: string | null;
  company: string | null; address: string | null; phone: string | null;
}): ProfileValues {
  return {
    email: u.email,
    first_name: u.first_name ?? "",
    last_name: u.last_name ?? "",
    company: u.company ?? "",
    address: u.address ?? "",
    phone: u.phone ?? "",
  };
}

const CHAMPS: { cle: keyof ProfileValues; label: string; type?: string }[] = [
  { cle: "last_name", label: "Nom" },
  { cle: "first_name", label: "Prénom" },
  { cle: "company", label: "Société" },
  { cle: "address", label: "Adresse" },
  { cle: "phone", label: "Téléphone", type: "tel" },
  { cle: "email", label: "E-mail", type: "email" },
];

/** Fiche d'identite. `mode="self"` -> PATCH /auth/me (et reconnexion si l'e-mail change) ;
 *  `mode="admin"` -> PATCH /admin/users/{userId}. */
export function ProfileDialog({
  mode,
  userId,
  initial,
  onClose,
}: {
  mode: "self" | "admin";
  userId?: string;
  initial: ProfileValues;
  onClose: () => void;
}) {
  const nav = useNavigate();
  const self = useUpdateProfile();
  const admin = useUpdateUser();
  const [v, setV] = useState<ProfileValues>(initial);
  const [error, setError] = useState("");

  const emailOk = /\S+@\S+/.test(v.email);
  const pending = self.isPending || admin.isPending;

  async function save() {
    setError("");
    try {
      if (mode === "self") {
        await self.mutateAsync(v);
        // L'e-mail (identifiant) a change -> le jeton porte l'ancien sub : on se reconnecte.
        if (v.email.trim().toLowerCase() !== initial.email.trim().toLowerCase()) {
          clearSession();
          nav("/login", { replace: true });
          return;
        }
      } else {
        await admin.mutateAsync({ id: userId!, ...v });
      }
      onClose();
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 409
          ? "Cet e-mail est déjà utilisé."
          : "Enregistrement impossible.",
      );
    }
  }

  return (
    <div
      className="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-4"
      onMouseDown={onClose}
    >
      <div
        className="w-full max-w-md rounded border bg-white p-6"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <h2 className="mb-4 font-semibold">Fiche — {initial.email}</h2>
        <div className="space-y-3">
          {CHAMPS.map((c) => (
            <label key={c.cle} className="block">
              <span className="text-xs text-gray-600">{c.label}</span>
              <input
                type={c.type ?? "text"}
                value={v[c.cle]}
                onChange={(e) => setV((s) => ({ ...s, [c.cle]: e.target.value }))}
                className="mt-1 w-full rounded border px-3 py-2 text-sm"
              />
            </label>
          ))}
        </div>

        {mode === "self" &&
          v.email.trim().toLowerCase() !== initial.email.trim().toLowerCase() && (
            <p className="mt-3 text-xs text-amber-700">
              Changer votre e-mail vous déconnectera : vous vous reconnecterez avec la
              nouvelle adresse.
            </p>
          )}

        {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

        <div className="mt-4 flex gap-2">
          <button onClick={onClose} className="flex-1 rounded border py-2 text-sm">
            Annuler
          </button>
          <button
            onClick={save}
            disabled={!emailOk || pending}
            className="flex-1 rounded bg-gray-900 py-2 text-sm text-white disabled:opacity-40"
          >
            {pending ? "…" : "Enregistrer"}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: « Mon profil » dans `AccountMenu`**

Dans `frontend/src/components/AccountMenu.tsx` :

Ajouter les imports :

```tsx
import { ProfileDialog, toProfileValues } from "./ProfileDialog";
```

Ajouter l'état (à côté de `pwOpen`) :

```tsx
  const [profileOpen, setProfileOpen] = useState(false);
```

Ajouter une entrée de menu **avant** « Changer mon mot de passe » (dans le `<div className="py-1">`) :

```tsx
            <MenuItem
              onClick={() => {
                setOpen(false);
                setProfileOpen(true);
              }}
            >
              Mon profil
            </MenuItem>
```

Et rendre le modal (à côté du `{pwOpen && <PasswordDialog … />}`), pré-rempli depuis `useMe` :

```tsx
      {profileOpen && me.data && (
        <ProfileDialog
          mode="self"
          initial={toProfileValues(me.data)}
          onClose={() => setProfileOpen(false)}
        />
      )}
```

- [ ] **Step 3: Action « Fiche » dans `Settings`**

Dans `frontend/src/pages/Settings.tsx`, composant `Row` :

Ajouter l'import en tête du fichier :

```tsx
import { ProfileDialog, toProfileValues } from "../components/ProfileDialog";
```

Dans `Row`, ajouter l'état (à côté de `editing`) :

```tsx
  const [fiche, setFiche] = useState(false);
```

Ajouter le bouton « Fiche » dans la cellule d'actions, **avant** « Modifier » :

```tsx
          <button onClick={() => setFiche(true)} className="text-xs text-gray-600 hover:underline">
            Fiche
          </button>
          <span className="mx-2 text-gray-300">·</span>
```

Et rendre le modal (juste avant le `{editing && (…)}` ou à côté, dans le fragment retourné par `Row`) :

```tsx
      {fiche && (
        <tr>
          <td colSpan={4} className="p-0">
            <ProfileDialog
              mode="admin"
              userId={user.id}
              initial={toProfileValues(user)}
              onClose={() => setFiche(false)}
            />
          </td>
        </tr>
      )}
```

(Le modal est en `position: fixed` : l'envelopper dans une ligne de table `<tr><td colSpan={4}>` garde un HTML de tableau valide sans décaler la mise en page.)

- [ ] **Step 4: Vérification frontend**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/frontend:/app" -w /app frontend sh -c "npm install --silent && npx tsc -b && npx vite build"`
Expected: `tsc` sans erreur (`toProfileValues` accepte aussi bien `Me` que `User` — mêmes champs), `vite build` réussi.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ProfileDialog.tsx frontend/src/components/AccountMenu.tsx frontend/src/pages/Settings.tsx
git commit -m "feat(front): fiche utilisateur (Mon profil + action Fiche admin)

Modal ProfileDialog reutilise en self (PATCH /auth/me, reconnexion si l e-mail change) et en
admin (PATCH /admin/users/{id}). Entree Mon profil dans le menu compte, action Fiche par
ligne sur Parametres."
```

---

## Vérification finale

- [ ] `docker compose ... api pytest` (run monté) — suite complète verte, dont `test_profile_me`, `test_admin_user_profile`, `test_admin_users`, et **`test_tenant_isolation` (bloquant)**.
- [ ] `ruff check app scripts tests` propre.
- [ ] Vérification frontend — `tsc -b` + `vite build` verts.
- [ ] **Contrôle réel navigateur** :
  - « Mon profil » (menu compte) ouvre la fiche pré-remplie, enregistre ; recharger montre les valeurs.
  - Changer son propre e-mail → déconnexion + reconnexion avec le nouvel e-mail.
  - En admin, « Fiche » sur une ligne édite un autre compte ; un e-mail déjà pris affiche le 409.
  - « Modifier » (rôle/domaines) fonctionne toujours (rétrocompatibilité).

## Ce que ce plan ne fait PAS, délibérément

- **Édition rôle/domaines depuis la Fiche** — reste dans « Modifier ».
- **Re-saisie du mot de passe pour changer l'e-mail** — hors périmètre.
- **Champs profil obligatoires** — optionnels.
- **Page/route « Mon profil » dédiée** — un modal suffit.
