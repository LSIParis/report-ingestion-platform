"""Sert la politique MTA-STS de chaque domaine surveillé.

https://mta-sts.<domaine>/.well-known/mta-sts.txt

Route PUBLIQUE : elle est appelée par les serveurs de messagerie du monde entier, pas par
un utilisateur. Elle ne divulgue rien qui ne soit déjà public par construction (une
politique MTA-STS est faite pour être lue par n'importe qui).

Le domaine est déduit du **Host**, jamais d'un paramètre : un domaine non surveillé reçoit
404, jamais la politique d'un autre.

La politique vient de la BASE. Auparavant elle était embarquée dans une image Docker :
ajouter un client imposait de modifier le dépôt, reconstruire et redéployer.
"""
import hashlib

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.db.models import Tenant
from app.db.session import tenant_scoped_session

router = APIRouter(tags=["mta-sts"])

# RFC 8461 §3.2 : lignes séparées par CRLF.
CRLF = "\r\n"


def render(tenant: Tenant) -> str:
    lines = [
        "version: STSv1",
        f"mode: {tenant.mta_sts_mode}",
        *[f"mx: {mx}" for mx in tenant.mta_sts_mx],
        f"max_age: {tenant.mta_sts_max_age}",
    ]
    return CRLF.join(lines) + CRLF


def policy_id(tenant: Tenant) -> str:
    """L'`id` à publier dans le TXT `_mta-sts`.

    Il DOIT changer dès que la politique change, sinon les expéditeurs gardent l'ancienne
    en cache jusqu'à expiration de max_age — et on ne peut rien y faire.

    On le dérive du CONTENU de la politique, pas d'un horodatage. Deux conséquences, les
    deux souhaitables : il change si et seulement si la politique change (impossible de
    l'oublier), et réenregistrer une politique identique n'oblige pas à retoucher le DNS
    pour rien. Un horodatage à la seconde échouait sur les deux plans : deux
    modifications dans la même seconde produisaient le même id.
    """
    return hashlib.sha256(render(tenant).encode()).hexdigest()[:20]


@router.get("/.well-known/mta-sts.txt", response_class=Response)
def mta_sts_policy(request: Request):
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if not host.startswith("mta-sts."):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "hôte inattendu")
    domain = host[len("mta-sts."):]

    with tenant_scoped_session(tenant_id=None, bypass=True) as db:
        tenant = db.query(Tenant).filter_by(domain=domain).first()

        # Pas de politique servie tant qu'elle n'est pas configurée. Un `mx:` vide serait
        # pire que pas de politique du tout : en mode enforce, AUCUN serveur ne
        # correspondrait et tout le courrier entrant serait refusé.
        if (not tenant or tenant.status != "active"
                or tenant.mta_sts_mode == "none" or not tenant.mta_sts_mx):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "aucune politique")

        body = render(tenant)

    return Response(
        content=body,
        media_type="text/plain; charset=utf-8",   # autre type = politique ignorée
        headers={"Cache-Control": "public, max-age=600"},
    )
