"""« Qui est cette IP, et pourquoi a-t-elle été rejetée ? »

C'est la seule question qui compte devant une IP en échec, et elle en cache une autre :
faut-il autoriser un service légitime qu'on a oublié, ou ignorer une usurpation ? Tant
qu'on ne sait pas trancher, le domaine reste bloqué en p=none — c'est-à-dire sans
protection.

## Le contrôle d'appartenance, et pourquoi il est écrit EN PREMIER

Le cache `ip_intel` n'a pas de `tenant_id` : ce sont des faits publics sur Internet. Mais
si on le laissait interroger librement, l'API deviendrait un oracle : « cette IP est-elle
dans votre cache ? » révélerait qu'un AUTRE client l'a vue passer. Fuite ténue, fuite
quand même.

D'où l'ordre, non négociable :

  1. l'IP apparaît-elle dans une ligne de rapport visible du tenant, SOUS RLS ?
  2. sinon → 404. Pas 403 : un 403 confirmerait l'existence. Pas de 200 appauvri non plus.
  3. seulement ensuite : le cache, puis le DNS.

On ne peut donc enquêter que sur une IP qu'on a déjà légitimement vue.
"""
from __future__ import annotations

import ipaddress
from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_

from app.auth.deps import get_db
from app.db.models import IpIntel, ReportRow, Tenant
from app.services import ip_intel, senders, spf

router = APIRouter(prefix="/ip-intel", tags=["ip-intel"])

# Les faits DNS bougent lentement. Une semaine suffit ; le bouton « réinterroger » est là
# pour les cas où l'exploitant vient justement de corriger quelque chose.
FRAICHEUR = timedelta(days=7)


def _rows_de_cette_ip(db, ip: str) -> list[ReportRow]:
    """Les lignes de rapport où cette IP apparaît — SOUS RLS.

    Deux champs, deux sens : `source_ip` est un expéditeur évalué par DMARC,
    `sending_mta_ip` un MTA qui a tenté une session TLS. On ne les confond pas — mais une
    IP qui échoue en TLS mérite la même enquête, et le tenant la voit dans ses rapports :
    la lui refuser par 404 serait absurde.

    Aucun `WHERE tenant_id` applicatif : la session est déjà scopée (CLAUDE.md). Une IP
    vue par un autre tenant ne renverra rien ici, et c'est exactement le but.
    """
    return (db.query(ReportRow)
              .filter(or_(ReportRow.data["source_ip"].astext == ip,
                          ReportRow.data["sending_mta_ip"].astext == ip))
              .all())


def _activite(rows: list[ReportRow]) -> dict:
    """Ce que CE tenant a réellement observé de cette IP.

    Souvent l'élément décisif : « 412 messages, 100 % en échec, tous sur votre
    header_from, aucune signature DKIM » ne se lit pas comme « 3 messages ».
    """
    messages = 0
    dispositions: Counter[str] = Counter()
    aligned: Counter[str] = Counter()
    spf_domains: set[str] = set()
    dkim_domains: set[str] = set()
    header_froms: set[str] = set()
    dates: list[str] = []
    tls_sessions = 0
    tls_failures: Counter[str] = Counter()

    for r in rows:
        d = r.data
        # La période couvre TOUTES les lignes, DMARC comme TLS : collectée avant le
        # `continue` ci-dessous, sinon une IP vue uniquement en TLS garde une période vide.
        if d.get("report_date"):
            dates.append(str(d["report_date"]))

        if d.get("kind") == "failure":
            n_tls = d.get("failure_sessions") or 0
            try:
                n_tls = int(n_tls)
            except (TypeError, ValueError):
                n_tls = 0
            tls_sessions += n_tls
            if d.get("result_type"):
                tls_failures[str(d["result_type"])] += n_tls
            continue
        n = d.get("message_count") or 0
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = 0
        messages += n

        if d.get("disposition"):
            dispositions[d["disposition"]] += n
        if d.get("aligned"):
            aligned[d["aligned"]] += n
        if d.get("auth_spf"):
            spf_domains.add(str(d["auth_spf"]))
        if d.get("auth_dkim"):
            dkim_domains.add(str(d["auth_dkim"]))
        if d.get("header_from"):
            header_froms.add(str(d["header_from"]))

    return {
        "messages": messages,
        "rows": len(rows),
        "first_seen": min(dates) if dates else None,
        "last_seen": max(dates) if dates else None,
        "dispositions": dict(dispositions),
        "aligned": dict(aligned),
        "spf_domains": sorted(spf_domains),
        "dkim_domains": sorted(dkim_domains),
        "header_froms": sorted(header_froms),
        "tls_sessions": tls_sessions,
        "tls_failures": dict(tls_failures),
    }


def _faits(db, ip: str, *, force: bool) -> IpIntel:
    """Le cache s'il est frais, sinon le DNS — puis on mémorise."""
    cached = db.get(IpIntel, ip)
    frais = (cached is not None and not force
             and cached.checked_at > datetime.now(timezone.utc) - FRAICHEUR)
    if frais:
        return cached

    facts = ip_intel.lookup(ip)          # n'échoue jamais : au pire, tout est None
    row = cached or IpIntel(ip=ip)
    row.ptr, row.fcrdns = facts.ptr, facts.fcrdns
    row.asn, row.as_org, row.country = facts.asn, facts.as_org, facts.country
    row.checked_at = datetime.now(timezone.utc)
    db.add(row)
    db.flush()
    return row


def _reponse(db, ip: str, *, force: bool) -> dict:
    rows = _rows_de_cette_ip(db, ip)
    if not rows:
        # 404, jamais 403 : un 403 confirmerait que l'IP existe ailleurs.
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "Cette IP n'apparaît dans aucun de vos rapports")

    cached = _faits(db, ip, force=force)
    facts = ip_intel.IpFacts(ip=ip, ptr=cached.ptr, fcrdns=cached.fcrdns,
                             asn=cached.asn, as_org=cached.as_org, country=cached.country)

    match = senders.identify(facts)      # l'appariement se fait ICI, pas en cache

    tenant = db.get(Tenant, rows[0].tenant_id)
    verdict = spf.covers(tenant.domain, ip) if tenant else spf.SpfVerdict("indetermine")

    return {
        "ip": ip,
        "ptr": cached.ptr,
        "fcrdns": cached.fcrdns,
        "asn": cached.asn,
        "as_org": cached.as_org,
        "country": cached.country,
        "checked_at": cached.checked_at.isoformat(),
        "sender": (None if match.sender is None else {
            "key": match.sender.key,
            "name": match.sender.name,
            "spf_include": match.sender.spf_include,
            "remediation": match.sender.remediation,
        }),
        "hosted_by": match.hosted_by,
        "spf": {"result": verdict.result, "mechanism": verdict.mechanism},
        "activity": _activite(rows),
    }


def _valide(ip: str) -> str:
    try:
        return str(ipaddress.ip_address(ip))
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Adresse IP invalide") from None


@router.get("/{ip}")
def get_ip_intel(ip: str, db=Depends(get_db)):
    return _reponse(db, _valide(ip), force=False)


@router.post("/{ip}/refresh")
def refresh_ip_intel(ip: str, db=Depends(get_db)):
    """Réinterroge le DNS immédiatement — même contrôle d'appartenance d'abord."""
    return _reponse(db, _valide(ip), force=True)
