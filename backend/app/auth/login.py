import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth.deps import get_tenant_ctx
from app.auth.emails import normalize_email
from app.auth.passwords import hash_password, verify_password
from app.config import settings
from app.db.models import AppUser, Tenant, UserTenant
from app.db.session import get_session, tenant_scoped_session
from app.services.audit import audit
from app.services.mailer import EmailNonEnvoye, send_email

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
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None
    address: str | None = None
    phone: str | None = None
    pending_email: str | None = None


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
            pending_email=user.pending_email if user else None,
        )


class ProfileIn(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None
    address: str | None = None
    phone: str | None = None


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
