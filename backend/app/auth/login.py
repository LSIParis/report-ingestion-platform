from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.auth.deps import get_tenant_ctx
from app.auth.passwords import hash_password, verify_password
from app.config import settings
from app.db.models import AppUser, Tenant, UserTenant
from app.db.session import get_session, tenant_scoped_session
from app.services.audit import audit

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn):
    with get_session() as db:
        user = db.query(AppUser).filter_by(email=body.email.lower()).first()
        if not user or not verify_password(body.password, user.password_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Identifiants invalides")
        tenant_ids = [
            str(t) for (t,) in db.query(UserTenant.tenant_id).filter_by(user_id=user.id).all()
        ]

    now = datetime.now(timezone.utc)
    claims = {
        "sub": user.email,
        "role": user.role,
        "tenant_ids": tenant_ids,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + timedelta(seconds=settings.jwt_ttl_seconds),
    }
    token = jwt.encode(claims, settings.jwt_private_key, algorithm="RS256")

    audit(actor=user.email, action="auth.login",
          tenant_id=tenant_ids[0] if len(tenant_ids) == 1 else None)
    return TokenOut(access_token=token, expires_in=settings.jwt_ttl_seconds)


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    domain: str
    name: str


class MeOut(BaseModel):
    email: str
    role: str
    tenants: list[TenantOut]


@router.get("/me", response_model=MeOut)
def me(ctx=Depends(get_tenant_ctx)):
    """Identité de l'utilisateur connecté et domaines auxquels il a droit.

    Nécessaire au sélecteur de domaine : le JWT ne contient que des UUID, et
    `/admin/tenants` est réservé aux administrateurs — un utilisateur rattaché à
    plusieurs domaines n'avait donc aucun moyen d'en connaître les noms.

    La liste est dérivée des `tenant_ids` du **jeton signé**, jamais d'un en-tête de
    requête : on ne peut pas s'ajouter un domaine en forgeant un appel.
    """
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        q = db.query(Tenant)
        if ctx.role != "platform_admin":
            q = q.filter(Tenant.id.in_(ctx.tenant_ids))
        tenants = q.order_by(Tenant.name).all()
    return MeOut(email=ctx.user, role=ctx.role,
                 tenants=[TenantOut.model_validate(t) for t in tenants])


class PasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, max_length=72)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(body: PasswordIn, ctx=Depends(get_tenant_ctx)):
    """Changement de mot de passe par l'utilisateur lui-même.

    Le mot de passe actuel est exigé : sans ça, un jeton volé (XSS, poste laissé ouvert)
    permettrait de verrouiller le compte de sa victime.

    Limite haute à 72 octets : c'est la limite dure de bcrypt, au-delà de laquelle le
    secret serait silencieusement tronqué (voir app/auth/passwords.py).
    """
    with get_session() as db:
        user = db.query(AppUser).filter_by(email=ctx.user).first()
        if not user or not verify_password(body.current_password, user.password_hash):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Mot de passe actuel incorrect")
        user.password_hash = hash_password(body.new_password)
        db.commit()

    audit(actor=ctx.user, action="auth.password_changed",
          tenant_id=ctx.active_tenant)
