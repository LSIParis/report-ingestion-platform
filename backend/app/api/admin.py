from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.auth.deps import get_tenant_ctx, require_role
from app.auth.passwords import hash_password
from app.db.models import AppUser, Tenant, TenantMatchingRule, UserTenant
from app.db.session import tenant_scoped_session
from app.services.audit import audit

router = APIRouter(prefix="/admin", tags=["admin"],
                   dependencies=[Depends(require_role("platform_admin"))])

ROLES = ("platform_admin", "tenant_viewer")


# ----------------------------------------------------------------- tenants & règles
@router.get("/tenants")
def list_tenants():
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        return [{"id": str(t.id), "domain": t.domain, "name": t.name}
                for t in db.query(Tenant).order_by(Tenant.name).all()]


@router.get("/tenants/{tenant_id}/matching-rules")
def list_rules(tenant_id: str):
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        return [{"id": str(r.id), "tenant_id": str(r.tenant_id), "rule_type": r.rule_type,
                 "pattern": r.pattern, "priority": r.priority, "is_active": r.is_active}
                for r in db.query(TenantMatchingRule).filter_by(tenant_id=tenant_id)
                           .order_by(TenantMatchingRule.priority).all()]


@router.post("/tenants/{tenant_id}/matching-rules", status_code=201)
def add_rule(tenant_id: str, rule_type: str, pattern: str, priority: int = 100):
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        r = TenantMatchingRule(tenant_id=tenant_id, rule_type=rule_type,
                               pattern=pattern, priority=priority, is_active=True)
        db.add(r)
        db.commit()
        return {"id": str(r.id)}


# ------------------------------------------------------------------------ comptes
class UserOut(BaseModel):
    id: UUID
    email: str
    role: str
    tenants: list[dict]
    created_at: datetime


class UserIn(BaseModel):
    email: str
    role: str
    # 72 octets : limite dure de bcrypt, au-delà le secret serait tronqué en silence.
    password: str = Field(min_length=12, max_length=72)
    tenant_ids: list[UUID] = []

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        # L'adresse n'est qu'un identifiant de connexion : on refuse l'évidemment
        # invalide, sans embarquer un validateur RFC 5322 complet pour autant.
        v = v.strip().lower()
        if "@" not in v or v.startswith("@") or v.endswith("@") or " " in v:
            raise ValueError("adresse e-mail invalide")
        return v


class UserPatch(BaseModel):
    role: str | None = None
    tenant_ids: list[UUID] | None = None


class PasswordReset(BaseModel):
    new_password: str = Field(min_length=12, max_length=72)


def _serialize(db, user: AppUser) -> dict:
    rows = (db.query(Tenant.id, Tenant.domain)
              .join(UserTenant, UserTenant.tenant_id == Tenant.id)
              .filter(UserTenant.user_id == user.id)
              .order_by(Tenant.domain).all())
    return {"id": user.id, "email": user.email, "role": user.role,
            "created_at": user.created_at,
            "tenants": [{"id": str(i), "domain": d} for i, d in rows]}


def _validate(role: str, tenant_ids: list[UUID]) -> None:
    if role not in ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Rôle invalide : {role}")
    # Un lecteur sans domaine ne verrait rien et l'API lui répondrait 403 à chaque
    # appel : c'est un compte mort-né, on refuse de le créer.
    if role == "tenant_viewer" and not tenant_ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Un compte en lecture doit être rattaché à au moins un domaine")


def _assert_not_self(ctx, user: AppUser, action: str) -> None:
    """Un administrateur ne peut ni se supprimer, ni se rétrograder.

    Sans ce garde-fou, une fausse manœuvre suffit à se verrouiller hors de sa propre
    plateforme — plus personne ne peut alors créer de compte ni lever une quarantaine,
    et il faut repasser par la console du conteneur pour s'en sortir.
    """
    if user.email == ctx.user:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Vous ne pouvez pas {action} votre propre compte")


@router.get("/users", response_model=list[UserOut])
def list_users():
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        return [_serialize(db, u) for u in db.query(AppUser).order_by(AppUser.email).all()]


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserIn, ctx=Depends(get_tenant_ctx)):
    _validate(body.role, body.tenant_ids)
    email = body.email

    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        if db.query(AppUser).filter_by(email=email).first():
            raise HTTPException(status.HTTP_409_CONFLICT, "Ce compte existe déjà")

        user = AppUser(email=email, role=body.role,
                       password_hash=hash_password(body.password))
        db.add(user)
        db.flush()
        for tid in body.tenant_ids:
            db.add(UserTenant(user_id=user.id, tenant_id=tid))
        db.flush()
        out = _serialize(db, user)
        db.commit()

    audit(actor=ctx.user, action="user.created", target_id=str(out["id"]),
          metadata={"email": email, "role": body.role})
    return out


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: str, body: UserPatch, ctx=Depends(get_tenant_ctx)):
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        user = db.get(AppUser, user_id)
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Compte introuvable")

        role = body.role or user.role
        if body.tenant_ids is None:
            current = [t for (t,) in db.query(UserTenant.tenant_id)
                                       .filter_by(user_id=user.id).all()]
        else:
            current = body.tenant_ids
        _validate(role, current)

        if body.role and body.role != user.role:
            _assert_not_self(ctx, user, "changer le rôle de")
            user.role = body.role

        if body.tenant_ids is not None:
            db.query(UserTenant).filter_by(user_id=user.id).delete()
            for tid in body.tenant_ids:
                db.add(UserTenant(user_id=user.id, tenant_id=tid))

        db.flush()
        out = _serialize(db, user)
        db.commit()

    audit(actor=ctx.user, action="user.updated", target_id=user_id,
          metadata={"role": out["role"], "tenants": len(out["tenants"])})
    return out


@router.post("/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(user_id: str, body: PasswordReset, ctx=Depends(get_tenant_ctx)):
    """Réinitialisation par un administrateur (l'utilisateur a perdu son mot de passe).
    Distincte de /auth/password, qui exige le mot de passe actuel : ici, c'est
    précisément parce qu'il est perdu qu'on ne peut pas l'exiger."""
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        user = db.get(AppUser, user_id)
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Compte introuvable")
        user.password_hash = hash_password(body.new_password)
        db.commit()

    audit(actor=ctx.user, action="user.password_reset", target_id=user_id)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: str, ctx=Depends(get_tenant_ctx)):
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        user = db.get(AppUser, user_id)
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Compte introuvable")
        _assert_not_self(ctx, user, "supprimer")

        email = user.email
        db.query(UserTenant).filter_by(user_id=user.id).delete()
        db.delete(user)
        db.commit()

    audit(actor=ctx.user, action="user.deleted", target_id=user_id,
          metadata={"email": email})
