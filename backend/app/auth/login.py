from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.auth.passwords import verify_password
from app.config import settings
from app.db.models import AppUser, UserTenant
from app.db.session import get_session
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
