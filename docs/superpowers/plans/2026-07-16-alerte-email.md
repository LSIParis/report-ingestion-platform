# Canal d'alerte e-mail — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter un canal d'alerte e-mail (`ALERT_CHANNEL=email`) qui envoie chaque alerte au destinataire propre du domaine (`tenant.alert_email`), en réutilisant `mailer.py`.

**Architecture:** Une colonne `alert_email` sur `tenant` ; un module `channels/email.py` respectant le contrat `envoyer(event, alert, tenant) -> bool` (False si non configuré ; `CanalIndisponible` si l'envoi échoue → Celery retente), enregistré `"email"` ; l'édition du destinataire sur la page Domaines admin.

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy 2.0 · Alembic · `smtplib` (via `mailer.py`) · pytest · React 19 + TypeScript · Vite.

**Spec:** `docs/superpowers/specs/2026-07-16-alerte-email-design.md`

## Global Constraints

- **Branche** : `feat/alerte-email` (déjà checkout), fraîche depuis `main` — qui contient déjà `app/services/mailer.py` (`send_email`/`EmailNonEnvoye`) et les migrations jusqu'à `0012`.
- **Contrat de canal** (`channels/base.py`) : `envoyer(event, alert, tenant) -> bool` — `True` = émis, `False` = **non configuré** (jamais un silence muet : on journalise), lève une **sous-classe de `CanalIndisponible`** si configuré mais l'envoi échoue (`workers/tasks.py` n'attrape QUE `CanalIndisponible`).
- **Destinataire par tenant** : `tenant.alert_email` = liste d'adresses séparées par des virgules (chacune *strippée*, vides ignorées) ; `NULL`/vide → pas d'alerte e-mail (log + `return False`).
- **Envoi sur `event == "opened"` ET `event == "closed"`** (pas de filtre par sévérité, contrairement à Desk365). Corps « Alerte OUVERTE »/« Alerte RÉSOLUE » selon l'event.
- **`ALERT_CHANNEL=email`** rend le canal actif via le registre `_CANAUX` — un seul canal à la fois, aucune nouvelle config globale. SMTP = les réglages `SMTP_*` du cycle 1.
- **Isolation (CLAUDE.md)** : le canal n'est appelé que par le worker (plan BYPASSRLS) ; l'édition passe par les routes **admin** existantes. `tests/test_tenant_isolation.py` reste vert.
- **`send_email` MOQUÉ dans les tests** (jamais de vrai SMTP). Commentaires/libellés en français. Messages de commit en français **sans accents**.
- **Back-end** : run monté (le conteneur `api` bake le code) — depuis `infra/`, Git Bash, `MSYS_NO_PATHCONV=1` :
  ```bash
  MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest <chemin> -q
  ```
  Migrations : ajouter `-e DATABASE_URL_MIGRATE=postgresql://postgres:postgres@postgres:5432/reports`. Lint CI = `ruff check app scripts tests` — **jamais** de `;` (E702) ni de corps sur la même ligne qu'un `def` (E704).
- **Front-end** : pas de test runner ; vérif = `tsc -b` + `vite build` :
  ```bash
  MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/frontend:/app" -w /app frontend sh -c "npm install --silent && npx tsc -b && npx vite build"
  ```

## Structure des fichiers

| Fichier | Rôle |
|---|---|
| `backend/app/db/models.py` | **Modifier.** Colonne `alert_email` sur `Tenant`. |
| `backend/migrations/versions/0013_tenant_alert_email.py` | **Créer.** La colonne (nullable). |
| `backend/app/services/alerting/channels/email.py` | **Créer.** Le canal e-mail. |
| `backend/app/services/alerting/channels/__init__.py` | **Modifier.** Enregistrer `"email"`. |
| `backend/tests/test_alert_channel_email.py` | **Créer.** Tests du canal (SMTP moqué). |
| `backend/app/api/admin.py` | **Modifier.** `TenantPatch.alert_email` + `update_tenant` + `list_tenants`. |
| `backend/tests/test_admin_tenant_alert_email.py` | **Créer.** `PATCH /admin/tenants/{id}` alert_email. |
| `frontend/src/api/domains.ts` | **Modifier.** `Domain.alert_email` + `useUpdateDomain`. |
| `frontend/src/pages/Domains.tsx` | **Modifier.** Édition « Alertes e-mail » par domaine. |

Ordre : 1 (colonne+migration) → 2 (canal) → 3 (admin) → 4 (frontend).

---

### Task 1: `tenant.alert_email` + migration `0013`

**Files:**
- Modify: `backend/app/db/models.py` (classe `Tenant`)
- Create: `backend/migrations/versions/0013_tenant_alert_email.py`

**Interfaces:**
- Produces: `Tenant.alert_email` (`Text`, nullable).

- [ ] **Step 1: Ajouter la colonne au modèle**

Dans `backend/app/db/models.py`, classe `Tenant`, après `mta_sts_max_age` (ou à la fin des colonnes de la classe) :

```python
    # Destinataire(s) des alertes e-mail pour ce domaine (liste separee par virgules).
    # Vide -> le canal e-mail n'envoie rien pour ce tenant (journalise, jamais un plantage).
    alert_email: Mapped[str | None] = mapped_column(Text)
```

- [ ] **Step 2: Écrire la migration**

Créer `backend/migrations/versions/0013_tenant_alert_email.py` :

```python
"""destinataire d'alerte e-mail par tenant (tenant.alert_email)

Revision ID: 0013_tenant_alert_email
Revises: 0012_email_verification
"""
import sqlalchemy as sa
from alembic import op

revision = "0013_tenant_alert_email"
down_revision = "0012_email_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant", sa.Column("alert_email", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenant", "alert_email")
```

- [ ] **Step 3: Appliquer et vérifier**

```bash
MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app -e DATABASE_URL_MIGRATE=postgresql://postgres:postgres@postgres:5432/reports api alembic upgrade head
docker compose -f infra/docker-compose.yml exec -T postgres psql -U postgres -d reports -c "\d tenant" | grep alert_email
```
Expected : la colonne `alert_email` apparaît.

- [ ] **Step 4: Commit**

```bash
git add backend/app/db/models.py backend/migrations/versions/0013_tenant_alert_email.py
git commit -m "feat(alerting): colonne tenant.alert_email + migration 0013

Destinataire(s) des alertes e-mail par domaine (liste separee par virgules). Nullable."
```

---

### Task 2: Le canal `channels/email.py`

**Files:**
- Create: `backend/app/services/alerting/channels/email.py`
- Modify: `backend/app/services/alerting/channels/__init__.py`
- Test: `backend/tests/test_alert_channel_email.py`

**Interfaces:**
- Consumes: `send_email`/`EmailNonEnvoye` (`app.services.mailer`), `CanalIndisponible` (`channels.base`), `tenant.alert_email` (Task 1).
- Produces: `envoyer(event: str, alert, tenant) -> bool` dans `channels/email.py` ; entrée `"email"` dans `_CANAUX`.

- [ ] **Step 1: Écrire les tests d'abord**

Créer `backend/tests/test_alert_channel_email.py` :

```python
"""Canal d'alerte e-mail : envoie a tenant.alert_email. send_email est MOQUE."""
from types import SimpleNamespace

import pytest

from app.services.alerting.channels import email as canal
from app.services.alerting.channels.base import CanalIndisponible


def _alert():
    return SimpleNamespace(kind="tls_failure", severity="critical",
                           payload={"sessions": 12, "mx": "mx.exemple.fr"})


def _tenant(alert_email):
    return SimpleNamespace(domain="exemple.fr", alert_email=alert_email)


def test_pas_de_destinataire_renvoie_false(monkeypatch):
    appels = []
    monkeypatch.setattr(canal, "send_email", lambda *a, **k: appels.append(a))
    assert canal.envoyer("opened", _alert(), _tenant(None)) is False
    assert appels == []


def test_envoi_ouverture(monkeypatch):
    vu = {}

    def faux(to, subject, body):
        vu["to"] = to
        vu["subject"] = subject
        vu["body"] = body

    monkeypatch.setattr(canal, "send_email", faux)
    assert canal.envoyer("opened", _alert(), _tenant("ops@exemple.fr")) is True
    assert vu["to"] == "ops@exemple.fr"
    assert "exemple.fr" in vu["subject"]
    assert "OUVERTE" in vu["body"]
    assert "tls_failure" in vu["body"]


def test_corps_resolue_a_la_fermeture(monkeypatch):
    vu = {}

    def faux(to, subject, body):
        vu["body"] = body

    monkeypatch.setattr(canal, "send_email", faux)
    canal.envoyer("closed", _alert(), _tenant("ops@exemple.fr"))
    assert "RÉSOLUE" in vu["body"]


def test_plusieurs_destinataires(monkeypatch):
    tos = []

    def faux(to, subject, body):
        tos.append(to)

    monkeypatch.setattr(canal, "send_email", faux)
    canal.envoyer("opened", _alert(), _tenant("a@x.fr, b@y.fr"))
    assert tos == ["a@x.fr", "b@y.fr"]


def test_echec_smtp_leve_canalindisponible(monkeypatch):
    from app.services.mailer import EmailNonEnvoye

    def echoue(*a, **k):
        raise EmailNonEnvoye("smtp ko")

    monkeypatch.setattr(canal, "send_email", echoue)
    with pytest.raises(CanalIndisponible):
        canal.envoyer("opened", _alert(), _tenant("ops@exemple.fr"))


def test_get_channel_email(monkeypatch):
    from app.config import settings
    from app.services.alerting.channels import get_channel

    monkeypatch.setattr(settings, "alert_channel", "email")
    assert get_channel() is canal
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest tests/test_alert_channel_email.py -q`
Expected: FAIL (`channels.email` inexistant).

- [ ] **Step 3: Écrire le canal**

Créer `backend/app/services/alerting/channels/email.py` :

```python
"""Canal d'alerte e-mail : envoie l'alerte au(x) destinataire(s) du tenant (alert_email).

Meme contrat que les autres canaux (channels/base.py) : True si envoye, False si non
configure (pas d'alert_email sur ce tenant -- journalise, jamais un silence muet), leve
EmailIndisponible (sous-classe de CanalIndisponible) si l'envoi echoue -> Celery retente.
Corps texte simple ; meme famille de libelles que le canal Desk365 (tables independantes).
"""
from __future__ import annotations

import structlog

from app.services.alerting.channels.base import CanalIndisponible
from app.services.mailer import EmailNonEnvoye, send_email

log = structlog.get_logger()

_NATURES = {
    "never_reported": "aucun rapport reçu depuis l'ajout du domaine",
    "domain_silent": "les rapports ont cessé d'arriver",
    "tls_failure": "échec de chiffrement TLS",
}


class EmailIndisponible(CanalIndisponible):
    """L'envoi de l'alerte par e-mail a echoue. Celery retentera."""


def _destinataires(tenant) -> list[str]:
    return [a.strip() for a in (tenant.alert_email or "").split(",") if a.strip()]


def _sujet(alert, tenant) -> str:
    quoi = _NATURES.get(alert.kind, alert.kind)
    return f"[DMARC] {tenant.domain} — {quoi}"


def _corps(event: str, alert, tenant) -> str:
    etat = "Alerte OUVERTE" if event == "opened" else "Alerte RÉSOLUE"
    lignes = [
        etat,
        "",
        f"Domaine : {tenant.domain}",
        f"Type : {alert.kind} ({alert.severity})",
        "",
        "Ce que la plateforme a constaté :",
    ]
    for cle, valeur in (alert.payload or {}).items():
        lignes.append(f"  - {cle} : {valeur}")
    lignes += ["", "Détail et historique : page Alertes de la plateforme."]
    return "\n".join(lignes)


def envoyer(event: str, alert, tenant) -> bool:
    dests = _destinataires(tenant)
    if not dests:
        log.warning("alerting.email_non_configure", alert_event=event,
                    kind=alert.kind, domain=tenant.domain)
        return False

    sujet = _sujet(alert, tenant)
    corps = _corps(event, alert, tenant)
    try:
        for adresse in dests:
            send_email(adresse, sujet, corps)
    except EmailNonEnvoye as exc:
        raise EmailIndisponible(str(exc)) from exc

    log.info("alerting.email_envoye", alert_event=event, kind=alert.kind,
             domain=tenant.domain, destinataires=len(dests))
    return True
```

- [ ] **Step 4: Enregistrer le canal**

Dans `backend/app/services/alerting/channels/__init__.py`, modifier l'import et le registre :

```python
from app.services.alerting.channels import desk365, email, webhook

_CANAUX = {"webhook": webhook, "desk365": desk365, "email": email}
```

- [ ] **Step 5: Lancer, vérifier que ça passe**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest tests/test_alert_channel_email.py -q`
Expected: PASS (6 tests).

- [ ] **Step 6: Lint + commit**

```bash
... api ruff check app/services/alerting/channels/email.py app/services/alerting/channels/__init__.py tests/test_alert_channel_email.py
git add backend/app/services/alerting/channels/email.py backend/app/services/alerting/channels/__init__.py backend/tests/test_alert_channel_email.py
git commit -m "feat(alerting): canal e-mail (ALERT_CHANNEL=email, destinataire par tenant)

envoyer() envoie a chaque adresse de tenant.alert_email ; non configure -> False journalise ;
EmailNonEnvoye -> EmailIndisponible (Celery retente). Ouverture et resolution. Enregistre email."
```

---

### Task 3: Admin — `TenantPatch.alert_email` + endpoints

**Files:**
- Modify: `backend/app/api/admin.py` (`TenantPatch`, `update_tenant`, `list_tenants`)
- Test: `backend/tests/test_admin_tenant_alert_email.py`

**Interfaces:**
- Consumes: `Tenant.alert_email` (Task 1).
- Produces: `PATCH /admin/tenants/{id}` accepte `alert_email` (validé, unicité non concernée) ; `list_tenants` et la réponse d'`update_tenant` renvoient `alert_email`.

- [ ] **Step 1: Écrire le test d'abord**

Créer `backend/tests/test_admin_tenant_alert_email.py` :

```python
"""PATCH /admin/tenants/{id} met a jour alert_email ; la liste le renvoie."""
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import router
from app.auth.middleware import TenantContext
from app.db.models import Tenant
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


def _make_tenant():
    with get_session() as db:
        t = Tenant(domain=f"al-{uuid.uuid4().hex[:8]}.test", name="Al")
        db.add(t)
        db.flush()
        tid = str(t.id)
        db.commit()
    return tid


def _cleanup(tid):
    with get_session() as db:
        db.query(Tenant).filter_by(id=tid).delete()
        db.commit()


def test_patch_alert_email_et_liste():
    tid = _make_tenant()
    try:
        c = _admin_client("admin@test.fr")
        r = c.patch(f"/admin/tenants/{tid}", json={"alert_email": "ops@exemple.fr, sec@exemple.fr"})
        assert r.status_code == 200
        assert r.json()["alert_email"] == "ops@exemple.fr, sec@exemple.fr"
        liste = c.get("/admin/tenants").json()
        ligne = next(x for x in liste if x["id"] == tid)
        assert ligne["alert_email"] == "ops@exemple.fr, sec@exemple.fr"
    finally:
        _cleanup(tid)


def test_patch_alert_email_vide_efface():
    tid = _make_tenant()
    try:
        c = _admin_client("admin@test.fr")
        c.patch(f"/admin/tenants/{tid}", json={"alert_email": "ops@exemple.fr"})
        r = c.patch(f"/admin/tenants/{tid}", json={"alert_email": ""})
        assert r.status_code == 200
        assert r.json()["alert_email"] is None
    finally:
        _cleanup(tid)


def test_patch_alert_email_invalide_422():
    tid = _make_tenant()
    try:
        c = _admin_client("admin@test.fr")
        r = c.patch(f"/admin/tenants/{tid}", json={"alert_email": "pasunemail"})
        assert r.status_code == 422
    finally:
        _cleanup(tid)
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest tests/test_admin_tenant_alert_email.py -q`
Expected: FAIL (`TenantPatch` n'a pas `alert_email` ; la réponse ne le renvoie pas).

- [ ] **Step 3: Étendre `TenantPatch`, `update_tenant`, `list_tenants`**

Dans `backend/app/api/admin.py` :

Remplacer `TenantPatch` par :

```python
class TenantPatch(BaseModel):
    name: str | None = None
    active: bool | None = None
    alert_email: str | None = None

    @field_validator("alert_email")
    @classmethod
    def _alert_email(cls, v: str | None) -> str | None:
        if v is None:
            return v
        for frag in v.split(","):
            frag = frag.strip()
            if frag and "@" not in frag:
                raise ValueError("adresse e-mail invalide dans la liste")
        return v
```

Dans `list_tenants`, ajouter `"alert_email"` au dict de chaque ligne (à côté de `"status"`) :

```python
                "alert_email": t.alert_email,
```

Dans `update_tenant`, appliquer `alert_email` — ajouter, **après** le bloc `if body.active is not None:` et **avant** la ligne `out = {...}` :

```python
        if body.alert_email is not None:
            tenant.alert_email = body.alert_email.strip() or None
```

et remplacer la ligne `out = {...}` (qui vaut aujourd'hui `{"id": ..., "domain": ..., "name": ..., "status": tenant.status}`) par la même chose **plus** `alert_email` — **sans retirer `status`** :

```python
        out = {"id": str(tenant.id), "domain": tenant.domain,
               "name": tenant.name, "status": tenant.status,
               "alert_email": tenant.alert_email}
```

- [ ] **Step 4: Lancer, vérifier que ça passe (+ tenants existant)**

```bash
MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" -w /app api pytest tests/test_admin_tenant_alert_email.py tests/test_admin_domains.py -q
```
Expected: PASS (les tests existants de `test_admin_domains.py` restent verts).

- [ ] **Step 5: Lint + commit**

```bash
... api ruff check app/api/admin.py tests/test_admin_tenant_alert_email.py
git add backend/app/api/admin.py backend/tests/test_admin_tenant_alert_email.py
git commit -m "feat(admin): PATCH /admin/tenants/{id} gere alert_email + la liste le renvoie

Champ optionnel valide (chaque fragment separe par virgule contient un @) ; vide -> NULL."
```

---

### Task 4: Front — édition « Alertes e-mail » sur la page Domaines

**Files:**
- Modify: `frontend/src/api/domains.ts` (`Domain`, `useUpdateDomain`)
- Modify: `frontend/src/pages/Domains.tsx` (composant ligne)

**Interfaces:**
- Consumes: `PATCH /admin/tenants/{id}` (Task 3).
- Produces: rien (feuille).

- [ ] **Step 1: `domains.ts` — `Domain.alert_email` + param du hook**

Dans `frontend/src/api/domains.ts` :

Ajouter à l'interface `Domain` (après `created_at`) :

```ts
  alert_email: string | null;
```

Élargir le type accepté par `useUpdateDomain` :

```ts
    mutationFn: ({ id, ...b }: { id: string; name?: string; active?: boolean; alert_email?: string }) =>
      api<Domain>(`/admin/tenants/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(b),
      }),
```

(Garder le reste de `useUpdateDomain` inchangé — l'invalidation existante.)

- [ ] **Step 2: `Domains.tsx` — action « Alertes » + ligne d'édition**

Dans `frontend/src/pages/Domains.tsx`, composant ligne (celui qui reçoit `domain: Domain` en prop, avec `useUpdateDomain`, `confirming`, etc.) :

Ajouter l'état (à côté de `confirming`) :

```tsx
  const [alerting, setAlerting] = useState(false);
  const [emails, setEmails] = useState(d.alert_email ?? "");
```

Ajouter un bouton **« Alertes »** dans la cellule d'actions, avant « Suspendre » :

```tsx
          <button onClick={() => setAlerting((a) => !a)} className="text-xs text-gray-600 hover:underline">
            Alertes
          </button>
          <span className="mx-2 text-gray-300">·</span>
```

Et, dans le fragment retourné (à côté des `<tr>` conditionnels `confirming`/`error`), ajouter la ligne d'édition :

```tsx
      {alerting && (
        <tr className="border-t bg-gray-50">
          <td colSpan={5} className="px-4 py-3">
            <label className="block text-sm">
              <span className="text-xs text-gray-600">
                Destinataire(s) des alertes e-mail — adresses séparées par des virgules
                (vide = aucune)
              </span>
              <input
                value={emails}
                onChange={(e) => setEmails(e.target.value)}
                placeholder="ops@client.fr, secu@client.fr"
                className="mt-1 w-full rounded border px-3 py-2 text-sm"
              />
            </label>
            <div className="mt-2 flex gap-2">
              <button
                onClick={() =>
                  update.mutate(
                    { id: d.id, alert_email: emails },
                    { onSuccess: () => setAlerting(false) },
                  )
                }
                className="rounded bg-gray-900 px-3 py-1.5 text-sm text-white"
              >
                Enregistrer
              </button>
              <button onClick={() => setAlerting(false)} className="rounded border px-3 py-1.5 text-sm">
                Annuler
              </button>
            </div>
          </td>
        </tr>
      )}
```

- [ ] **Step 3: Vérification frontend**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/frontend:/app" -w /app frontend sh -c "npm install --silent && npx tsc -b && npx vite build"`
Expected: `tsc` sans erreur (`Domain.alert_email` typé, `useUpdateDomain` accepte `alert_email`), `vite build` réussi.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/domains.ts frontend/src/pages/Domains.tsx
git commit -m "feat(front): edition du destinataire d alerte e-mail par domaine (page Domaines)

Un bouton Alertes ouvre un champ pour saisir la/les adresse(s) (separees par virgules),
enregistre via PATCH /admin/tenants/{id}."
```

---

## Vérification finale

- [ ] `docker compose ... api pytest` (run monté) — suite complète verte, dont `test_alert_channel_email`, `test_admin_tenant_alert_email`, `test_admin_domains`, et **`test_tenant_isolation` (bloquant)**.
- [ ] `ruff check app scripts tests` propre.
- [ ] Vérification frontend — `tsc -b` + `vite build` verts.
- [ ] **Contrôle réel navigateur / prod** :
  - Sur Domaines, « Alertes » enregistre un destinataire ; recharger le montre.
  - Avec `ALERT_CHANNEL=email` + `SMTP_*` réglés en prod, une alerte ouverte arrive par e-mail au bon destinataire ; un domaine sans `alert_email` n'envoie rien (log), sans planter le balayage.

## Ce que ce plan ne fait PAS, délibérément

- **Multi-canal simultané** — un seul `ALERT_CHANNEL` actif.
- **Destinataire global** — le destinataire est par tenant.
- **Gabarits HTML** — corps texte simple.
- **Rate-limiting** — la déduplication des alertes borne déjà le volume.
