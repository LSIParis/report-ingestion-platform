from dataclasses import dataclass

import jwt
from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings


@dataclass(frozen=True)
class TenantContext:
    user: str
    role: str
    tenant_ids: tuple[str, ...]
    active_tenant: str | None
    bypass: bool


class TenantMiddleware(BaseHTTPMiddleware):
    """Vérifie le JWT, en dérive le TenantContext, le pose sur request.state.
    Ne fait aucune requête DB — juste l'authZ."""

    # /ingest/* : appelé par AWS SNS (pas de JWT) → sécurisé par signature SNS.
    PUBLIC_PATHS = ("/health", "/auth/login", "/docs", "/openapi.json", "/redoc", "/ingest/")

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(self.PUBLIC_PATHS):
            return await call_next(request)

        # Une HTTPException levée DANS un BaseHTTPMiddleware n'est pas interceptée par
        # les gestionnaires de FastAPI (ils vivent plus bas dans la pile) : elle
        # ressortirait en 500. Le refus d'isolation doit être un 403 explicite, pas une
        # erreur serveur — on convertit donc ici, à la frontière.
        try:
            token = self._bearer(request)
            try:
                claims = jwt.decode(
                    token, settings.jwt_public_key, algorithms=["RS256"],
                    audience=settings.jwt_audience, issuer=settings.jwt_issuer,
                )
            except jwt.PyJWTError as exc:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"JWT invalide: {exc}")

            request.state.tenant = self._build_context(request, claims)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

        return await call_next(request)

    @staticmethod
    def _bearer(request: Request) -> str:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bearer token manquant")
        return auth[7:]

    @staticmethod
    def _build_context(request: Request, claims: dict) -> TenantContext:
        role = claims.get("role", "tenant_viewer")
        tenant_ids = tuple(claims.get("tenant_ids", []))

        if role == "platform_admin":
            wanted = request.headers.get("X-Tenant-Id")
            return TenantContext(user=claims["sub"], role=role, tenant_ids=tenant_ids,
                                 active_tenant=wanted, bypass=(wanted is None))

        if not tenant_ids:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Aucun tenant rattaché")

        wanted = request.headers.get("X-Tenant-Id")
        if wanted is None:
            active = tenant_ids[0] if len(tenant_ids) == 1 else None
            if active is None:
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    "X-Tenant-Id requis (multi-tenant)")
        else:
            # CŒUR DE L'ISOLATION : le tenant demandé DOIT être dans le token signé.
            if wanted not in tenant_ids:
                raise HTTPException(status.HTTP_403_FORBIDDEN,
                                    "Tenant non autorisé pour cet utilisateur")
            active = wanted

        return TenantContext(user=claims["sub"], role=role, tenant_ids=tenant_ids,
                             active_tenant=active, bypass=False)
