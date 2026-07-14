"""Création d'un domaine surveillé, et sa règle de résolution DMARC.

Utilisé par `scripts.add_tenant` ET par l'API d'administration : les deux chemins
doivent créer exactement la même chose, sinon un domaine créé depuis l'interface se
comporterait différemment d'un domaine créé en console.
"""
from __future__ import annotations

import re

from sqlalchemy import select

from app.db.models import Tenant, TenantMatchingRule


def dmarc_subject_pattern(domain: str) -> str:
    """Motif reconnaissant les rapports DMARC de ce domaine dans le SUJET.

    Jamais de règle `sender` : pour DMARC, l'expéditeur est toujours google.com ou
    microsoft.com, quel que soit le domaine concerné — une telle règle enverrait les
    rapports de TOUS les clients dans un seul tenant.

    `(?![\\w.-])` interdit les suffixes trompeurs : 'acme.com' ne doit pas matcher
    'acme.com.evil.tld'. Le préfixe est couvert par 'domain:\\s*'.
    """
    return rf"domain:\s*{re.escape(domain)}(?![\w.-])"


def ensure_tenant(db, domain: str, name: str | None = None) -> tuple[Tenant, bool]:
    """Crée le domaine, sa règle de résolution et sa politique MTA-STS. Renvoie
    (tenant, créé). Idempotent : rejouable sans produire de doublon."""
    domain = domain.strip().lower()
    tenant = db.execute(select(Tenant).filter_by(domain=domain)).scalar_one_or_none()
    created = tenant is None

    if created:
        tenant = Tenant(domain=domain, name=(name or domain).strip())
        # Politique MTA-STS préremplie depuis le MX RÉEL du domaine. Le `mx:` doit
        # correspondre au CERTIFICAT du MX, pas à son nom : Microsoft 365 présente
        # *.mail.protection.outlook.com. S'y tromper, en mode enforce, fait perdre du
        # courrier — d'où la déduction automatique plutôt qu'une saisie à la main.
        #
        # On démarre en `testing` : les expéditeurs SIGNALENT les échecs sans bloquer.
        # Passer directement en enforce sur un domaine jamais observé, c'est prendre le
        # risque de couper la réception de courrier d'un client.
        from app.services.onboarding import mx_policy_for, resolve_mx
        mx = mx_policy_for(resolve_mx(domain))
        tenant.mta_sts_mx = mx
        tenant.mta_sts_mode = "testing" if mx else "none"
        db.add(tenant)
        db.flush()

    pattern = dmarc_subject_pattern(domain)
    rule = db.execute(select(TenantMatchingRule).filter_by(
        tenant_id=tenant.id, rule_type="subject_regex",
        pattern=pattern)).scalar_one_or_none()
    if not rule:
        db.add(TenantMatchingRule(tenant_id=tenant.id, rule_type="subject_regex",
                                  pattern=pattern, priority=20, is_active=True))
        db.flush()

    return tenant, created


def set_tenant_active(db, tenant: Tenant, active: bool) -> None:
    """Active ou suspend un domaine.

    Suspendre désactive aussi ses règles de résolution : sans ça, le pipeline
    continuerait à lui attribuer les nouveaux rapports, et « suspendu » ne voudrait
    rien dire. Les données déjà collectées restent intactes.
    """
    tenant.status = "active" if active else "suspended"
    (db.query(TenantMatchingRule)
       .filter_by(tenant_id=tenant.id)
       .update({"is_active": active}, synchronize_session=False))
