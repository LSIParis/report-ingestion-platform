# E-mail sortant — Cycle 1 — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construire une couche d'envoi SMTP et vérifier l'ownership de la nouvelle adresse (code envoyé + confirmé) avant d'appliquer un changement d'e-mail de compte par l'utilisateur.

**Architecture:** Un mailer `smtplib` générique ; l'e-mail sort de `PATCH /auth/me` et passe par deux endpoints `request`/`confirm` avec un état « en attente » (code haché + expiration + essais) sur `app_user` ; le front sépare identité (immédiate) et e-mail (sous-flux code → reconnexion). L'admin reste immédiat.

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy 2.0 · Alembic · `smtplib` · Pydantic · pytest · React 19 + TypeScript · Vite · TanStack Query.

**Spec:** `docs/superpowers/specs/2026-07-15-email-sortant-cycle1-verification-design.md`

## Global Constraints

- **Branche** : ce lot se construit **sur `feat/fiche-utilisateur`** (déjà checkout, non mergée), il en consomme le code (`ProfileIn`, `update_me`, `ProfileDialog`, colonnes d'identité d'`app_user`, `normalize_email`).
- **Isolation (CLAUDE.md)** : `app_user` n'est pas tenant-scoped (auth) — pas de RLS. Les endpoints `email/request`/`confirm` ne touchent **que** le compte porteur du jeton (`ctx.user`), jamais `role`/`tenant_ids`. `tests/test_tenant_isolation.py` reste vert.
- **Envoi SMTP moqué dans les tests** : jamais de vrai serveur ; on espionne/moque `send_email`.
- **Code** : 6 chiffres (`secrets.randbelow(1_000_000)`, `%06d`), **haché** (`hash_password`/`verify_password`), expiration **15 min**, **5 essais** max. Envoi **avant** persistance de l'attente (pas d'attente orpheline).
- **E-mail hors `PATCH /auth/me`** : `ProfileIn` perd `email` (identité seule) ; l'admin (`PATCH /admin/users/{id}`) reste inchangé (immédiat).
- **Après confirmation** : le front `clearSession()` + `/login` (le jeton portait l'ancien e-mail).
- Commentaires/libellés en français. Messages de commit en français **sans accents**.
- **Back-end** : run monté (le conteneur `api` bake le code) — depuis `infra/`, Git Bash, `MSYS_NO_PATHCONV=1` :
  ```bash
  MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest <chemin> -q
  ```
  Migrations : ajouter `-e DATABASE_URL_MIGRATE=postgresql://postgres:postgres@postgres:5432/reports` pour `alembic`. Lint CI = `ruff check app scripts tests` — **jamais** de `;` multi-instructions (E702), **jamais** de corps sur la même ligne qu'un `def` (E704).
- **Front-end** : pas de test runner ; vérif = `tsc -b` + `vite build` :
  ```bash
  MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/frontend:/app" -w /app frontend sh -c "npm install --silent && npx tsc -b && npx vite build"
  ```

## Structure des fichiers

| Fichier | Rôle |
|---|---|
| `backend/app/config.py` | **Modifier.** Réglages `smtp_*`. |
| `backend/app/services/mailer.py` | **Créer.** `send_email` + `EmailNonEnvoye`. |
| `backend/tests/test_mailer.py` | **Créer.** Non configuré + envoi moqué. |
| `backend/app/db/models.py` | **Modifier.** 4 colonnes « en attente » sur `AppUser`. |
| `backend/migrations/versions/0012_email_verification.py` | **Créer.** Les colonnes. |
| `backend/app/auth/login.py` | **Modifier.** `ProfileIn` sans e-mail ; `MeOut`+`pending_email` ; endpoints `request`/`confirm`. |
| `backend/tests/test_profile_me.py` | **Modifier.** `PATCH /me` ne change plus l'e-mail. |
| `backend/tests/test_email_change.py` | **Créer.** Flux `request`/`confirm`. |
| `frontend/src/api/account.ts` | **Modifier.** `useUpdateProfile` (5 champs), `useRequestEmailChange`, `useConfirmEmailChange`, `Me.pending_email`. |
| `frontend/src/components/ProfileDialog.tsx` | **Modifier.** Sépare identité (immédiate) et e-mail (sous-flux code). |

Ordre : 1 (mailer) → 2 (colonnes) → 3 (`/auth/me` sans e-mail + `MeOut`) → 4 (request/confirm) → 5 (front : api + `ProfileDialog` ensemble). Les deux derniers fichiers front sont **une seule tâche** (le composant consomme les hooks modifiés).

---

### Task 1: Config SMTP + mailer

**Files:**
- Modify: `backend/app/config.py`
- Create: `backend/app/services/mailer.py`
- Test: `backend/tests/test_mailer.py`

**Interfaces:**
- Produces: `send_email(to: str, subject: str, body: str) -> None` et `class EmailNonEnvoye(Exception)` dans `app.services.mailer`. Réglages `settings.smtp_host/smtp_port/smtp_user/smtp_password/smtp_from`.

- [ ] **Step 1: Écrire les tests d'abord**

Créer `backend/tests/test_mailer.py` :

```python
"""mailer.send_email : envoi SMTP, source unique. On ne parle jamais a un vrai serveur."""
import pytest

from app.services import mailer
from app.services.mailer import EmailNonEnvoye, send_email


def test_smtp_non_configure_leve(monkeypatch):
    monkeypatch.setattr(mailer.settings, "smtp_host", "")
    with pytest.raises(EmailNonEnvoye):
        send_email("x@y.fr", "sujet", "corps")


def test_envoi_appelle_smtp(monkeypatch):
    monkeypatch.setattr(mailer.settings, "smtp_host", "smtp.test")
    monkeypatch.setattr(mailer.settings, "smtp_port", 587)
    monkeypatch.setattr(mailer.settings, "smtp_user", "u")
    monkeypatch.setattr(mailer.settings, "smtp_password", "p")
    monkeypatch.setattr(mailer.settings, "smtp_from", "no-reply@lsiparis.tech")
    vu = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=10):
            vu["host"] = host
            vu["port"] = port

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            vu["tls"] = True

        def login(self, user, password):
            vu["login"] = user

        def send_message(self, msg):
            vu["to"] = msg["To"]
            vu["from"] = msg["From"]
            vu["subject"] = msg["Subject"]

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    send_email("dest@y.fr", "Sujet", "Corps")
    assert vu["host"] == "smtp.test"
    assert vu["tls"] is True
    assert vu["login"] == "u"
    assert vu["to"] == "dest@y.fr"
    assert vu["from"] == "no-reply@lsiparis.tech"
    assert vu["subject"] == "Sujet"


def test_echec_smtp_leve(monkeypatch):
    monkeypatch.setattr(mailer.settings, "smtp_host", "smtp.test")

    class FakeSMTP:
        def __init__(self, *a, **k):
            raise OSError("connexion refusee")

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    with pytest.raises(EmailNonEnvoye):
        send_email("x@y.fr", "s", "c")
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest tests/test_mailer.py -q`
Expected: FAIL (`app.services.mailer` inexistant).

- [ ] **Step 3: Ajouter la config SMTP**

Dans `backend/app/config.py`, dans la classe `Settings` (après le bloc IMAP), ajouter :

```python
    # --- Envoi SMTP (e-mail sortant : verification e-mail, alertes) ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "no-reply@lsiparis.tech"
```

- [ ] **Step 4: Écrire le mailer**

Créer `backend/app/services/mailer.py` :

```python
"""Envoi d'e-mail sortant (SMTP), source UNIQUE.

Utilise pour la verification de changement d'e-mail (et, au cycle 2, le canal d'alerte
e-mail). Toute impossibilite d'envoi -- SMTP non configure, erreur reseau/SMTP -- leve
EmailNonEnvoye : l'appelant la traduit en erreur claire, jamais un plantage silencieux.
Le corps n'est jamais journalise (il peut contenir un code).
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage

import structlog

from app.config import settings

log = structlog.get_logger()


class EmailNonEnvoye(Exception):
    """L'e-mail n'a pas pu etre envoye (SMTP non configure ou echec de l'envoi)."""


def send_email(to: str, subject: str, body: str) -> None:
    if not settings.smtp_host:
        raise EmailNonEnvoye("SMTP non configure (smtp_host vide)")

    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        log.warning("email_non_envoye", to=to, error=str(exc))
        raise EmailNonEnvoye(f"echec SMTP : {exc}") from exc
```

- [ ] **Step 5: Lancer, vérifier que ça passe**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest tests/test_mailer.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Lint + commit**

```bash
... api ruff check app/config.py app/services/mailer.py tests/test_mailer.py
git add backend/app/config.py backend/app/services/mailer.py backend/tests/test_mailer.py
git commit -m "feat(mail): couche d envoi SMTP (send_email + EmailNonEnvoye)

smtplib + STARTTLS, config smtp_*. Non configure ou echec -> EmailNonEnvoye, jamais un
plantage silencieux. Le corps n est jamais journalise."
```

---

### Task 2: Colonnes « en attente » sur `app_user` + migration `0012`

**Files:**
- Modify: `backend/app/db/models.py` (classe `AppUser`)
- Create: `backend/migrations/versions/0012_email_verification.py`

**Interfaces:**
- Produces: sur `app_user` : `pending_email` (`Text` nullable), `email_code_hash` (`Text` nullable), `email_code_expires_at` (`DateTime(timezone=True)` nullable), `email_code_attempts` (`Integer` NOT NULL défaut 0).

- [ ] **Step 1: Ajouter les colonnes au modèle**

Dans `backend/app/db/models.py`, classe `AppUser`, après les colonnes d'identité (`phone`) ajoutées par la branche fiche :

```python
    pending_email: Mapped[str | None] = mapped_column(Text)
    email_code_hash: Mapped[str | None] = mapped_column(Text)
    email_code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_code_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
```

(`Text`, `Integer`, `DateTime`, `text`, `datetime` sont déjà importés dans `models.py`.)

- [ ] **Step 2: Écrire la migration**

Créer `backend/migrations/versions/0012_email_verification.py` :

```python
"""etat "changement d'e-mail en attente" sur app_user

Revision ID: 0012_email_verification
Revises: 0011_user_profile
"""
import sqlalchemy as sa
from alembic import op

revision = "0012_email_verification"
down_revision = "0011_user_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_user", sa.Column("pending_email", sa.Text(), nullable=True))
    op.add_column("app_user", sa.Column("email_code_hash", sa.Text(), nullable=True))
    op.add_column("app_user", sa.Column("email_code_expires_at",
                                        sa.DateTime(timezone=True), nullable=True))
    op.add_column("app_user", sa.Column("email_code_attempts", sa.Integer(),
                                        nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    for col in ("email_code_attempts", "email_code_expires_at", "email_code_hash",
                "pending_email"):
        op.drop_column("app_user", col)
```

- [ ] **Step 3: Appliquer et vérifier**

```bash
MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app -e DATABASE_URL_MIGRATE=postgresql://postgres:postgres@postgres:5432/reports api alembic upgrade head
docker compose -f infra/docker-compose.yml exec -T postgres psql -U postgres -d reports -c "\d app_user" | grep -E "pending_email|email_code"
```
Expected : les 4 colonnes apparaissent.

- [ ] **Step 4: Commit**

```bash
git add backend/app/db/models.py backend/migrations/versions/0012_email_verification.py
git commit -m "feat(auth): etat changement d e-mail en attente sur app_user + migration 0012

pending_email, email_code_hash, email_code_expires_at, email_code_attempts (defaut 0).
L e-mail de connexion ne change pas tant que le code n est pas confirme."
```

---

### Task 3: `PATCH /auth/me` sans e-mail + `MeOut.pending_email`

**Files:**
- Modify: `backend/app/auth/login.py`
- Test: `backend/tests/test_profile_me.py`

**Interfaces:**
- Consumes: colonnes de la Task 2.
- Produces: `ProfileIn` sans `email` (5 champs d'identité) ; `update_me` met à jour l'identité seule ; `MeOut` gagne `pending_email: str | None` ; `GET /auth/me` le renvoie.

- [ ] **Step 1: Adapter les tests existants**

Dans `backend/tests/test_profile_me.py` :

- Le test `test_patch_me_met_a_jour_l_identite` envoie `{"email": email, ...}` : **retirer** la clé `email` du JSON (l'e-mail ne passe plus par là). Le corps devient `{"first_name": "Ada", "last_name": "Lovelace", "company": "LSI", "address": "1 rue X", "phone": "0600000000"}` et les assertions sur `first_name`/`company`/`phone` restent.
- Le test `test_patch_me_email_deja_pris_409` et `test_patch_me_email_mis_en_minuscules` : **supprimer** (le changement d'e-mail ne passe plus par `PATCH /me` — il est couvert par `test_email_change.py` en Task 4).
- **Ajouter** un test prouvant que `PATCH /me` **ignore** un e-mail passé et **ne le change pas** :

```python
def test_patch_me_ne_change_pas_l_email():
    email = f"me-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(email)
    try:
        c = _client(email)
        r = c.patch("/auth/me", json={"email": "autre@test.fr", "first_name": "Ada"})
        assert r.status_code == 204
        b = c.get("/auth/me").json()
        assert b["email"] == email          # inchange
        assert b["first_name"] == "Ada"
        assert b["pending_email"] is None
    finally:
        _cleanup(email)
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest tests/test_profile_me.py -q`
Expected: FAIL (`pending_email` absent de la réponse ; `PATCH /me` change encore l'e-mail).

- [ ] **Step 3: Retirer l'e-mail de `ProfileIn`/`update_me`, ajouter `pending_email` à `MeOut`**

Dans `backend/app/auth/login.py` :

Remplacer `class ProfileIn(...)` (retirer `email` et son validateur) par :

```python
class ProfileIn(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None
    address: str | None = None
    phone: str | None = None
```

Remplacer le corps de `update_me` (retirer le bloc e-mail) par :

```python
@router.patch("/me", status_code=status.HTTP_204_NO_CONTENT)
def update_me(body: ProfileIn, ctx=Depends(get_tenant_ctx)):
    """Mise a jour de SA PROPRE identite. Ne touche NI l'e-mail (voir /me/email/*), NI le
    role, NI les domaines. Compte resolu par ctx.user (e-mail du jeton signe)."""
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        user = db.query(AppUser).filter_by(email=ctx.user).first()
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Compte introuvable")
        user.first_name = body.first_name or None
        user.last_name = body.last_name or None
        user.company = body.company or None
        user.address = body.address or None
        user.phone = body.phone or None
        db.commit()

    audit(actor=ctx.user, action="user.profile_updated", tenant_id=ctx.active_tenant)
```

Ajouter `pending_email` à `MeOut` (après `phone`) :

```python
    pending_email: str | None = None
```

Et dans `me()`, ajouter au constructeur `MeOut(...)` :

```python
            pending_email=user.pending_email if user else None,
```

- [ ] **Step 4: Lancer, vérifier que ça passe**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest tests/test_profile_me.py -q`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
... api ruff check app/auth/login.py tests/test_profile_me.py
git add backend/app/auth/login.py backend/tests/test_profile_me.py
git commit -m "feat(auth): PATCH /auth/me ne change plus l e-mail ; MeOut expose pending_email

Le changement d e-mail passe desormais par /auth/me/email/request+confirm (verifie).
ProfileIn n a plus le champ email ; identite seule, immediate."
```

---

### Task 4: Endpoints `email/request` + `email/confirm`

**Files:**
- Modify: `backend/app/auth/login.py`
- Test: `backend/tests/test_email_change.py`

**Interfaces:**
- Consumes: colonnes (Task 2), `send_email`/`EmailNonEnvoye` (Task 1), `normalize_email`, `hash_password`/`verify_password`.
- Produces: `POST /auth/me/email/request` (`{new_email}` → 202 ; 400 même adresse ; 409 pris ; 502 envoi échoué) ; `POST /auth/me/email/confirm` (`{code}` → 204 appliqué ; 400 code faux/expiré/aucun ; 429 >5 essais ; 409 pris entre-temps).

- [ ] **Step 1: Écrire les tests d'abord**

Créer `backend/tests/test_email_change.py` :

```python
"""POST /auth/me/email/request + confirm : verification par code (envoi SMTP moque)."""
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.auth.login as login_mod
from app.auth.login import router
from app.auth.middleware import TenantContext
from app.auth.passwords import hash_password
from app.db.models import AppUser
from app.db.session import get_session


@pytest.fixture
def boite(monkeypatch):
    """Capture l'e-mail au lieu de l'envoyer (aucun SMTP reel)."""
    vu = {}

    def faux_envoi(to, subject, body):
        vu["to"] = to
        vu["subject"] = subject
        vu["body"] = body

    monkeypatch.setattr(login_mod, "send_email", faux_envoi)
    return vu


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
        db.commit()


def _cleanup(*emails):
    with get_session() as db:
        for e in emails:
            db.query(AppUser).filter_by(email=e).delete()
        db.commit()


def _code(body):
    return re.search(r"\d{6}", body).group()


def test_request_envoie_code_et_pose_attente(boite):
    old = f"a-{uuid.uuid4().hex[:8]}@test.fr"
    new = f"b-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(old)
    try:
        r = _client(old).post("/auth/me/email/request", json={"new_email": new})
        assert r.status_code == 202
        assert boite["to"] == new
        assert re.search(r"\d{6}", boite["body"])
        with get_session() as db:
            u = db.query(AppUser).filter_by(email=old).first()
            assert u.pending_email == new       # attente posee
            assert u.email == old                # e-mail de connexion INCHANGE
    finally:
        _cleanup(old, new)


def test_request_meme_email_400(boite):
    old = f"a-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(old)
    try:
        r = _client(old).post("/auth/me/email/request", json={"new_email": old})
        assert r.status_code == 400
    finally:
        _cleanup(old)


def test_request_email_pris_409(boite):
    old = f"a-{uuid.uuid4().hex[:8]}@test.fr"
    autre = f"c-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(old)
    _make_user(autre)
    try:
        r = _client(old).post("/auth/me/email/request", json={"new_email": autre})
        assert r.status_code == 409
    finally:
        _cleanup(old, autre)


def test_request_smtp_echec_502(monkeypatch):
    from app.services.mailer import EmailNonEnvoye

    def echoue(*a, **k):
        raise EmailNonEnvoye("smtp ko")

    monkeypatch.setattr(login_mod, "send_email", echoue)
    old = f"a-{uuid.uuid4().hex[:8]}@test.fr"
    new = f"b-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(old)
    try:
        r = _client(old).post("/auth/me/email/request", json={"new_email": new})
        assert r.status_code == 502
        with get_session() as db:
            u = db.query(AppUser).filter_by(email=old).first()
            assert u.pending_email is None      # rien d orpheline
    finally:
        _cleanup(old, new)


def test_confirm_bon_code_applique(boite):
    old = f"a-{uuid.uuid4().hex[:8]}@test.fr"
    new = f"b-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(old)
    try:
        c = _client(old)
        c.post("/auth/me/email/request", json={"new_email": new})
        r = c.post("/auth/me/email/confirm", json={"code": _code(boite["body"])})
        assert r.status_code == 204
        with get_session() as db:
            assert db.query(AppUser).filter_by(email=new).first() is not None
            u = db.query(AppUser).filter_by(email=new).first()
            assert u.pending_email is None      # purge
    finally:
        _cleanup(old, new)


def test_confirm_mauvais_code_400_incremente(boite):
    old = f"a-{uuid.uuid4().hex[:8]}@test.fr"
    new = f"b-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(old)
    try:
        c = _client(old)
        c.post("/auth/me/email/request", json={"new_email": new})
        r = c.post("/auth/me/email/confirm", json={"code": "000000"})
        assert r.status_code == 400
        with get_session() as db:
            u = db.query(AppUser).filter_by(email=old).first()
            assert u.email_code_attempts == 1
            assert u.email == old               # non applique
    finally:
        _cleanup(old, new)


def test_confirm_cinq_essais_429(boite):
    old = f"a-{uuid.uuid4().hex[:8]}@test.fr"
    new = f"b-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(old)
    try:
        c = _client(old)
        c.post("/auth/me/email/request", json={"new_email": new})
        for _ in range(5):
            c.post("/auth/me/email/confirm", json={"code": "000000"})
        r = c.post("/auth/me/email/confirm", json={"code": _code(boite["body"])})
        assert r.status_code == 429            # meme le bon code est refuse apres 5 essais
    finally:
        _cleanup(old, new)


def test_confirm_expire_400(boite):
    old = f"a-{uuid.uuid4().hex[:8]}@test.fr"
    new = f"b-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(old)
    try:
        c = _client(old)
        c.post("/auth/me/email/request", json={"new_email": new})
        with get_session() as db:
            u = db.query(AppUser).filter_by(email=old).first()
            u.email_code_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            db.commit()
        r = c.post("/auth/me/email/confirm", json={"code": _code(boite["body"])})
        assert r.status_code == 400
    finally:
        _cleanup(old, new)
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest tests/test_email_change.py -q`
Expected: FAIL (endpoints inexistants → 404/405).

- [ ] **Step 3: Ajouter les endpoints**

Dans `backend/app/auth/login.py`, ajouter les imports en tête (avec les autres) :

```python
import secrets
```

et (avec les `from app.` existants) :

```python
from app.services.mailer import EmailNonEnvoye, send_email
```

(`secrets`, `datetime`/`timedelta`/`timezone`, `hash_password`/`verify_password`, `normalize_email`, `field_validator` sont déjà importés — vérifier et compléter au besoin.)

Ajouter, après l'endpoint `update_me` (avant `class PasswordIn`) :

```python
CODE_TTL = timedelta(minutes=15)
MAX_CODE_ATTEMPTS = 5


class EmailRequestIn(BaseModel):
    new_email: str

    @field_validator("new_email")
    @classmethod
    def _email(cls, v: str) -> str:
        return normalize_email(v)


class EmailConfirmIn(BaseModel):
    code: str


def _purge_pending(user) -> None:
    user.pending_email = None
    user.email_code_hash = None
    user.email_code_expires_at = None
    user.email_code_attempts = 0


@router.post("/me/email/request", status_code=status.HTTP_202_ACCEPTED)
def request_email_change(body: EmailRequestIn, ctx=Depends(get_tenant_ctx)):
    """Demande de changement d'e-mail : envoie un code a la NOUVELLE adresse. Rien n'est
    ecrit tant que l'envoi n'a pas reussi (pas d'attente orpheline)."""
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        user = db.query(AppUser).filter_by(email=ctx.user).first()
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Compte introuvable")
        if body.new_email == user.email:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "C'est deja votre adresse")
        if db.query(AppUser).filter(AppUser.email == body.new_email,
                                    AppUser.id != user.id).first():
            raise HTTPException(status.HTTP_409_CONFLICT, "Cet e-mail est deja utilise")

        code = f"{secrets.randbelow(1_000_000):06d}"
        try:
            send_email(
                body.new_email,
                "Confirmation de votre nouvelle adresse e-mail",
                f"Votre code de confirmation est : {code}\n\n"
                "Il expire dans 15 minutes. Si vous n'etes pas a l'origine de cette "
                "demande, ignorez ce message.",
            )
        except EmailNonEnvoye as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                                "Impossible d'envoyer le code, reessayez.") from exc

        user.pending_email = body.new_email
        user.email_code_hash = hash_password(code)
        user.email_code_expires_at = datetime.now(timezone.utc) + CODE_TTL
        user.email_code_attempts = 0
        db.commit()

    audit(actor=ctx.user, action="user.email_change_requested", tenant_id=ctx.active_tenant)


@router.post("/me/email/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_email_change(body: EmailConfirmIn, ctx=Depends(get_tenant_ctx)):
    """Confirme le code -> applique le changement d'e-mail. Le front se reconnecte ensuite."""
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        user = db.query(AppUser).filter_by(email=ctx.user).first()
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Compte introuvable")

        expire = (user.email_code_expires_at is None
                  or user.email_code_expires_at < datetime.now(timezone.utc))
        if not user.pending_email or not user.email_code_hash or expire:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "Aucun changement d'e-mail en attente ou code expire.")
        if user.email_code_attempts >= MAX_CODE_ATTEMPTS:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                "Trop d'essais, redemandez un code.")
        if not verify_password(body.code, user.email_code_hash):
            user.email_code_attempts += 1
            db.commit()
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Code incorrect.")

        # Course : la nouvelle adresse a pu etre prise entre la demande et la confirmation.
        if db.query(AppUser).filter(AppUser.email == user.pending_email,
                                    AppUser.id != user.id).first():
            _purge_pending(user)
            db.commit()
            raise HTTPException(status.HTTP_409_CONFLICT, "Cet e-mail est deja utilise")

        user.email = user.pending_email
        _purge_pending(user)
        db.commit()

    audit(actor=ctx.user, action="user.email_changed", tenant_id=ctx.active_tenant)
```

- [ ] **Step 4: Lancer, vérifier que ça passe**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest tests/test_email_change.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Lint + commit**

```bash
... api ruff check app/auth/login.py tests/test_email_change.py
git add backend/app/auth/login.py backend/tests/test_email_change.py
git commit -m "feat(auth): changement d e-mail verifie par code (request + confirm)

Code 6 chiffres haché, expire 15 min, 5 essais max ; envoi avant persistance ; unicite
re-verifiee a la confirmation. L e-mail de connexion ne bouge qu au 204."
```

---

### Task 5: Front — hooks API + `ProfileDialog` (identité immédiate, e-mail vérifié)

**Files:**
- Modify: `frontend/src/api/account.ts`, `frontend/src/components/ProfileDialog.tsx`

**Interfaces:**
- Consumes: `PATCH /auth/me`, `POST /auth/me/email/request`, `POST /auth/me/email/confirm`, `useUpdateUser` (admin, inchangé), `clearSession`, `ApiError`.
- Produces:
  - `Me` gagne `pending_email: string | null` ; `useUpdateProfile()` envoie **5 champs** ; `useRequestEmailChange()`/`useConfirmEmailChange()`.
  - `ProfileDialog` réécrit : identité immédiate + sous-flux e-mail (code) ; signature exportée `ProfileDialog({ mode, userId, initial, onClose })` et `toProfileValues` **inchangées** (`AccountMenu`/`Settings` intacts).

> **API et composant sont une SEULE tâche** : le composant consomme les hooks modifiés, donc les changer séparément casserait `tsc` entre les deux. On ne vérifie/committe qu'après les deux.

- [ ] **Step 1: Adapter `account.ts`**

Dans `frontend/src/api/account.ts` :

Ajouter à l'interface `Me` (après `phone`) :

```ts
  pending_email: string | null;
```

Remplacer le corps de `mutationFn` de `useUpdateProfile` (retirer `email`) :

```ts
    mutationFn: (body: {
      first_name: string; last_name: string; company: string; address: string; phone: string;
    }) =>
      api<void>("/auth/me", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
```

Ajouter les deux hooks (après `useUpdateProfile`) :

```ts
export const useRequestEmailChange = () =>
  useMutation({
    mutationFn: (body: { new_email: string }) =>
      api<void>("/auth/me/email/request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
  });

export const useConfirmEmailChange = () =>
  useMutation({
    mutationFn: (body: { code: string }) =>
      api<void>("/auth/me/email/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
  });
```

- [ ] **Step 2: Réécrire `ProfileDialog.tsx`**

Remplacer tout le contenu de `frontend/src/components/ProfileDialog.tsx` par :

```tsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  useConfirmEmailChange,
  useRequestEmailChange,
  useUpdateProfile,
} from "../api/account";
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

/** Convertit les `null` de l'API en `""` pour le formulaire. Accepte `Me` comme `User`. */
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

const IDENTITE: { cle: keyof ProfileValues; label: string; type?: string }[] = [
  { cle: "last_name", label: "Nom" },
  { cle: "first_name", label: "Prénom" },
  { cle: "company", label: "Société" },
  { cle: "address", label: "Adresse" },
  { cle: "phone", label: "Téléphone", type: "tel" },
];

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
        {mode === "admin" ? (
          <AdminForm userId={userId!} initial={initial} onClose={onClose} />
        ) : (
          <SelfForm initial={initial} onClose={onClose} />
        )}
      </div>
    </div>
  );
}

/* Admin : identite + e-mail, immediat (PATCH /admin/users/{id}). */
function AdminForm({
  userId,
  initial,
  onClose,
}: {
  userId: string;
  initial: ProfileValues;
  onClose: () => void;
}) {
  const update = useUpdateUser();
  const [v, setV] = useState<ProfileValues>(initial);
  const [error, setError] = useState("");
  const emailOk = /\S+@\S+/.test(v.email);

  async function save() {
    setError("");
    try {
      await update.mutateAsync({ id: userId, ...v });
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
    <div className="space-y-3">
      {IDENTITE.map((c) => (
        <Champ key={c.cle} label={c.label} type={c.type}
               value={v[c.cle]} onChange={(x) => setV((s) => ({ ...s, [c.cle]: x }))} />
      ))}
      <Champ label="E-mail" type="email" value={v.email}
             onChange={(x) => setV((s) => ({ ...s, email: x }))} />
      {error && <p className="text-sm text-red-600">{error}</p>}
      <Actions onCancel={onClose} onSave={save}
               disabled={!emailOk || update.isPending} pending={update.isPending} />
    </div>
  );
}

/* Self : identite immediate (PATCH /auth/me) + changement d'e-mail verifie par code. */
function SelfForm({ initial, onClose }: { initial: ProfileValues; onClose: () => void }) {
  const nav = useNavigate();
  const updateProfile = useUpdateProfile();
  const requestEmail = useRequestEmailChange();
  const confirmEmail = useConfirmEmailChange();

  const [v, setV] = useState<ProfileValues>(initial);
  const [error, setError] = useState("");

  const [etape, setEtape] = useState<"idle" | "email" | "code">("idle");
  const [newEmail, setNewEmail] = useState("");
  const [code, setCode] = useState("");
  const [emailErr, setEmailErr] = useState("");

  async function saveIdentite() {
    setError("");
    try {
      await updateProfile.mutateAsync({
        first_name: v.first_name,
        last_name: v.last_name,
        company: v.company,
        address: v.address,
        phone: v.phone,
      });
      onClose();
    } catch {
      setError("Enregistrement impossible.");
    }
  }

  async function envoyerCode() {
    setEmailErr("");
    try {
      await requestEmail.mutateAsync({ new_email: newEmail });
      setEtape("code");
    } catch (e) {
      setEmailErr(
        e instanceof ApiError && e.status === 409
          ? "Cet e-mail est déjà utilisé."
          : e instanceof ApiError && e.status === 502
            ? "Impossible d'envoyer le code, réessayez."
            : e instanceof ApiError && e.status === 400
              ? "C'est déjà votre adresse."
              : "Demande impossible.",
      );
    }
  }

  async function confirmer() {
    setEmailErr("");
    try {
      await confirmEmail.mutateAsync({ code });
      // L'e-mail (identifiant) a change -> le jeton porte l'ancien sub : reconnexion.
      clearSession();
      nav("/login", { replace: true });
    } catch (e) {
      setEmailErr(
        e instanceof ApiError && e.status === 429
          ? "Trop d'essais, redemandez un code."
          : e instanceof ApiError && e.status === 409
            ? "Cet e-mail vient d'être pris."
            : "Code incorrect ou expiré.",
      );
    }
  }

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        {IDENTITE.map((c) => (
          <Champ key={c.cle} label={c.label} type={c.type}
                 value={v[c.cle]} onChange={(x) => setV((s) => ({ ...s, [c.cle]: x }))} />
        ))}
        {error && <p className="text-sm text-red-600">{error}</p>}
        <Actions onCancel={onClose} onSave={saveIdentite}
                 disabled={updateProfile.isPending} pending={updateProfile.isPending}
                 label="Enregistrer l'identité" />
      </div>

      <div className="border-t pt-4">
        <div className="text-xs text-gray-600">E-mail de connexion</div>
        <div className="mt-1 flex items-center justify-between gap-2">
          <span className="text-sm">{initial.email}</span>
          {etape === "idle" && (
            <button
              onClick={() => {
                setNewEmail("");
                setEmailErr("");
                setEtape("email");
              }}
              className="text-xs text-blue-600 hover:underline"
            >
              Changer l'e-mail
            </button>
          )}
        </div>

        {etape === "email" && (
          <div className="mt-3 space-y-2">
            <Champ label="Nouvel e-mail" type="email" value={newEmail} onChange={setNewEmail} />
            <p className="text-xs text-amber-700">
              Un code sera envoyé à cette adresse pour la vérifier. Après confirmation, vous
              serez déconnecté et vous reconnecterez avec la nouvelle adresse.
            </p>
            {emailErr && <p className="text-sm text-red-600">{emailErr}</p>}
            <div className="flex gap-2">
              <button onClick={() => setEtape("idle")} className="rounded border px-3 py-1.5 text-sm">
                Annuler
              </button>
              <button
                onClick={envoyerCode}
                disabled={!/\S+@\S+/.test(newEmail) || requestEmail.isPending}
                className="rounded bg-gray-900 px-3 py-1.5 text-sm text-white disabled:opacity-40"
              >
                {requestEmail.isPending ? "…" : "Envoyer le code"}
              </button>
            </div>
          </div>
        )}

        {etape === "code" && (
          <div className="mt-3 space-y-2">
            <p className="text-sm">
              Un code a été envoyé à <strong>{newEmail}</strong>.
            </p>
            <Champ label="Code (6 chiffres)" value={code} onChange={setCode} />
            {emailErr && <p className="text-sm text-red-600">{emailErr}</p>}
            <div className="flex gap-2">
              <button onClick={envoyerCode} disabled={requestEmail.isPending}
                      className="rounded border px-3 py-1.5 text-sm">
                Renvoyer le code
              </button>
              <button
                onClick={confirmer}
                disabled={code.length < 6 || confirmEmail.isPending}
                className="rounded bg-gray-900 px-3 py-1.5 text-sm text-white disabled:opacity-40"
              >
                {confirmEmail.isPending ? "…" : "Confirmer"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Champ({
  label,
  type,
  value,
  onChange,
}: {
  label: string;
  type?: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label className="block">
      <span className="text-xs text-gray-600">{label}</span>
      <input
        type={type ?? "text"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full rounded border px-3 py-2 text-sm"
      />
    </label>
  );
}

function Actions({
  onCancel,
  onSave,
  disabled,
  pending,
  label,
}: {
  onCancel: () => void;
  onSave: () => void;
  disabled: boolean;
  pending: boolean;
  label?: string;
}) {
  return (
    <div className="flex gap-2">
      <button onClick={onCancel} className="flex-1 rounded border py-2 text-sm">
        Annuler
      </button>
      <button
        onClick={onSave}
        disabled={disabled}
        className="flex-1 rounded bg-gray-900 py-2 text-sm text-white disabled:opacity-40"
      >
        {pending ? "…" : label ?? "Enregistrer"}
      </button>
    </div>
  );
}
```

- [ ] **Step 3: Vérification frontend**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/frontend:/app" -w /app frontend sh -c "npm install --silent && npx tsc -b && npx vite build"`
Expected: `tsc` sans erreur (les hooks de l'étape 1 typent tout ; `AccountMenu`/`Settings` inchangés compilent), `vite build` réussi.

- [ ] **Step 4: Commit (les deux fichiers ensemble)**

```bash
git add frontend/src/api/account.ts frontend/src/components/ProfileDialog.tsx
git commit -m "feat(front): fiche self separe identite (immediate) et e-mail (verifie par code)

useUpdateProfile n envoie plus l e-mail (5 champs) ; useRequestEmailChange/useConfirmEmailChange.
ProfileDialog : identite via PATCH /auth/me ; e-mail via sous-flux « Changer l e-mail » ->
code -> confirmation -> reconnexion. Admin inchange (immediat)."
```

---

## Vérification finale

- [ ] `docker compose ... api pytest` (run monté) — suite complète verte, dont `test_mailer`, `test_profile_me`, `test_email_change`, et **`test_tenant_isolation` (bloquant)**.
- [ ] `ruff check app scripts tests` propre.
- [ ] Vérification frontend — `tsc -b` + `vite build` verts.
- [ ] **Contrôle réel navigateur** :
  - « Mon profil » : l'identité s'enregistre immédiatement.
  - « Changer l'e-mail » → un code arrive à la nouvelle adresse ; un mauvais code est refusé ; le bon code applique + déconnecte ; on se reconnecte avec le nouvel e-mail.
  - Un e-mail déjà pris affiche le 409 ; SMTP indisponible affiche « Impossible d'envoyer le code ».
  - En admin, « Fiche » change un e-mail sans code (immédiat).

## Ce que ce plan ne fait PAS, délibérément

- **Le canal d'alerte e-mail** — cycle 2 (réutilisera `mailer.py`).
- **Lien de confirmation** au lieu du code — non retenu.
- **Vérification du changement d'e-mail admin** — l'admin reste immédiat.
- **Rate-limiting fin des renvois** — écrasement + expiration + plafond d'essais suffisent.
