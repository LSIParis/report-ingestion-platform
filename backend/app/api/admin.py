import re
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func

from app.auth.deps import get_tenant_ctx, require_role
from app.auth.passwords import hash_password
from app.db.models import AppUser, Email, Report, Tenant, TenantMatchingRule, UserTenant
from app.db.session import tenant_scoped_session
from app.services.audit import audit
from app.services.rules import RuleError
from app.services.rules import validate as validate_rule
from app.services.tenants import ensure_tenant, set_tenant_active
from app.tenant_resolver.resolver import TenantResolverService
from app.workers.tasks import reprocess_report

router = APIRouter(prefix="/admin", tags=["admin"],
                   dependencies=[Depends(require_role("platform_admin"))])

ROLES = ("platform_admin", "tenant_viewer")


# --------------------------------------------------------------- domaines surveillés
class TenantIn(BaseModel):
    domain: str
    name: str | None = None

    @field_validator("domain")
    @classmethod
    def _domain(cls, v: str) -> str:
        v = v.strip().lower().rstrip(".")
        if v.startswith("@"):            # confusion fréquente : on saisit une adresse
            v = v[1:]
        if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+", v):
            raise ValueError("nom de domaine invalide (attendu : exemple.com)")
        return v


class TenantPatch(BaseModel):
    name: str | None = None
    active: bool | None = None


@router.get("/tenants")
def list_tenants():
    """Domaines surveillés, avec ce qu'ils ont réellement collecté.

    Le volume et la date du dernier rapport sont ce qui permet de repérer un domaine
    silencieux — le symptôme d'un enregistrement DMARC mal publié.
    """
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        stats = dict(
            (tid, (n, last)) for tid, n, last in
            db.query(Report.tenant_id, func.count(Report.id), func.max(Report.created_at))
              .group_by(Report.tenant_id).all()
        )
        rules = dict(
            db.query(TenantMatchingRule.tenant_id, func.count())
              .filter_by(is_active=True).group_by(TenantMatchingRule.tenant_id).all()
        )
        out = []
        for t in db.query(Tenant).order_by(Tenant.domain).all():
            reports, last = stats.get(t.id, (0, None))
            out.append({
                "id": str(t.id), "domain": t.domain, "name": t.name,
                "status": t.status,
                "reports": reports,
                "last_report_at": last.isoformat() if last else None,
                "active_rules": rules.get(t.id, 0),
                "created_at": t.created_at.isoformat(),
            })
        return out


@router.post("/tenants", status_code=status.HTTP_201_CREATED)
def create_tenant(body: TenantIn, ctx=Depends(get_tenant_ctx)):
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        if db.query(Tenant).filter_by(domain=body.domain).first():
            raise HTTPException(status.HTTP_409_CONFLICT, "Ce domaine est déjà surveillé")
        tenant, _ = ensure_tenant(db, body.domain, body.name)
        out = {"id": str(tenant.id), "domain": tenant.domain, "name": tenant.name}
        db.commit()

    audit(actor=ctx.user, action="tenant.created", target_id=out["id"],
          metadata={"domain": out["domain"]})
    return out


@router.patch("/tenants/{tenant_id}")
def update_tenant(tenant_id: str, body: TenantPatch, ctx=Depends(get_tenant_ctx)):
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        tenant = db.get(Tenant, tenant_id)
        if not tenant:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Domaine introuvable")
        if body.name is not None:
            tenant.name = body.name.strip() or tenant.domain
        if body.active is not None:
            set_tenant_active(db, tenant, body.active)
        out = {"id": str(tenant.id), "domain": tenant.domain,
               "name": tenant.name, "status": tenant.status}
        db.commit()

    audit(actor=ctx.user, action="tenant.updated", target_id=tenant_id, metadata=out)
    return out


@router.delete("/tenants/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tenant(tenant_id: str, ctx=Depends(get_tenant_ctx)):
    """Suppression définitive — uniquement si le domaine n'a jamais rien collecté.

    Dès qu'un e-mail lui est rattaché, le supprimer effacerait l'historique du client
    (rapports, lignes, pièces jointes) : on refuse, et on oriente vers la suspension,
    qui coupe la collecte sans rien détruire.
    """
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        tenant = db.get(Tenant, tenant_id)
        if not tenant:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Domaine introuvable")

        emails = db.query(func.count()).select_from(Email).filter(
            Email.tenant_id == tenant.id).scalar()
        if emails:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Ce domaine a déjà collecté {emails} e-mail(s). Suspendez-le plutôt "
                "que de le supprimer : la suppression effacerait tout son historique.")
        if db.query(func.count()).select_from(UserTenant).filter(
                UserTenant.tenant_id == tenant.id).scalar():
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "Des comptes sont rattachés à ce domaine.")

        domain = tenant.domain
        db.query(TenantMatchingRule).filter_by(tenant_id=tenant.id).delete()
        db.delete(tenant)
        db.commit()

    audit(actor=ctx.user, action="tenant.deleted", target_id=tenant_id,
          metadata={"domain": domain})


@router.post("/quarantine/requeue", status_code=status.HTTP_202_ACCEPTED)
def requeue_quarantine(ctx=Depends(get_tenant_ctx)):
    """Rejoue les e-mails restés sans domaine attribué.

    Cas courant : le client publie son DMARC avant que le domaine n'existe ici. Ses
    rapports s'accumulent en quarantaine, invisibles de tous — la plateforme refuse de
    deviner. Une fois le domaine créé, ce bouton les rattache.
    """
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        ids = [str(e.id) for e in db.query(Email.id)
               .filter(Email.status == "needs_review").all()]

    for email_id in ids:
        reprocess_report.delay(email_id)

    audit(actor=ctx.user, action="quarantine.requeued", metadata={"count": len(ids)})
    return {"requeued": len(ids)}


# ------------------------------------------------------- règles de résolution
# La cascade évalue les types dans CET ordre, et s'arrête au premier qui matche.
# L'ordre n'est pas cosmétique : une règle `sender` court-circuite toutes les autres.
CASCADE = {"sender": 0, "subject_regex": 1, "keyword": 2, "alias": 3}


class RuleIn(BaseModel):
    tenant_id: UUID
    rule_type: str
    pattern: str
    priority: int = Field(default=100, ge=1, le=1000)


class RulePatch(BaseModel):
    is_active: bool | None = None
    priority: int | None = Field(default=None, ge=1, le=1000)


class RuleTestIn(BaseModel):
    subject: str = ""
    from_address: str = ""


@router.get("/rules")
def list_rules():
    """Toutes les règles, dans l'ORDRE D'ÉVALUATION réel.

    On ne les liste pas par domaine : l'effet d'une règle dépend de toutes les autres
    (la cascade s'arrête au premier match). Une règle vue isolément ne dit rien de ce
    qu'elle fait réellement.
    """
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        rows = (db.query(TenantMatchingRule, Tenant.domain)
                  .join(Tenant, Tenant.id == TenantMatchingRule.tenant_id).all())
        out = [{"id": str(r.id), "tenant_id": str(r.tenant_id), "domain": d,
                "rule_type": r.rule_type, "pattern": r.pattern,
                "priority": r.priority, "is_active": r.is_active}
               for r, d in rows]
    return sorted(out, key=lambda r: (CASCADE.get(r["rule_type"], 9), r["priority"],
                                      r["domain"]))


@router.post("/rules", status_code=status.HTTP_201_CREATED)
def add_rule(body: RuleIn, ctx=Depends(get_tenant_ctx)):
    try:
        pattern = validate_rule(body.rule_type, body.pattern)
    except RuleError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        if not db.get(Tenant, body.tenant_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Domaine introuvable")
        r = TenantMatchingRule(tenant_id=body.tenant_id, rule_type=body.rule_type,
                               pattern=pattern, priority=body.priority, is_active=True)
        db.add(r)
        db.flush()
        out = {"id": str(r.id)}
        db.commit()

    audit(actor=ctx.user, action="rule.created", target_id=out["id"],
          metadata={"type": body.rule_type, "pattern": pattern})
    return out


@router.patch("/rules/{rule_id}")
def update_rule(rule_id: str, body: RulePatch, ctx=Depends(get_tenant_ctx)):
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        r = db.get(TenantMatchingRule, rule_id)
        if not r:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Règle introuvable")
        if body.is_active is not None:
            r.is_active = body.is_active
        if body.priority is not None:
            r.priority = body.priority
        out = {"id": str(r.id), "is_active": r.is_active, "priority": r.priority}
        db.commit()

    audit(actor=ctx.user, action="rule.updated", target_id=rule_id, metadata=out)
    return out


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: str, ctx=Depends(get_tenant_ctx)):
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        r = db.get(TenantMatchingRule, rule_id)
        if not r:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Règle introuvable")
        meta = {"type": r.rule_type, "pattern": r.pattern}
        db.delete(r)
        db.commit()

    audit(actor=ctx.user, action="rule.deleted", target_id=rule_id, metadata=meta)


@router.post("/rules/test")
def test_rules(body: RuleTestIn):
    """Banc d'essai : à quel domaine CE message serait-il attribué ?

    Rejoue la cascade réelle, sans rien écrire. C'est le seul moyen de vérifier une
    règle avant qu'elle ne se mette à ranger de vraies données — et de comprendre
    pourquoi un rapport part en quarantaine.
    """
    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        rules = (db.query(TenantMatchingRule).filter_by(is_active=True)
                   .order_by(TenantMatchingRule.priority.asc()).all())
        match = TenantResolverService()._match(body.from_address, body.subject, rules)
        domain = None
        if match.tenant_id:
            t = db.get(Tenant, match.tenant_id)
            domain = t.domain if t else None

    return {"tenant_id": match.tenant_id, "domain": domain,
            "method": match.method, "confidence": round(match.confidence, 3),
            "quarantined": match.tenant_id is None}


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
