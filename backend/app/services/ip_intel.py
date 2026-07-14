"""Les faits DNS sur une IP source : qui est-ce, réellement.

Une IP nue ne répond à aucune des questions qu'on se pose en la lisant. Ce module en
extrait ce qu'Internet en dit publiquement — sans API tierce, sans clé, sans qu'aucune
donnée de nos clients ne sorte : on pose à Internet une question sur une IP qui est
elle-même déjà publique.

Deux précautions traversent tout le fichier :

 - **Le PTR seul ne prouve rien.** Le propriétaire d'une IP y écrit ce qu'il veut, y
   compris « mail.google.com ». Seul l'aller-retour (FCrDNS) — le nom, réinterrogé,
   redonne-t-il cette IP ? — distingue un vrai hébergeur d'un menteur. Tout ce qui est
   bâti sur le PTR (le catalogue, notamment) doit exiger `fcrdns`.

 - **Aucun signal ne fait échouer la requête.** Un timeout DNS vaut « inconnu », jamais
   une exception qui viderait l'écran. L'absence d'information est une information ;
   une erreur 500 n'en est pas une.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass

import dns.exception
import dns.resolver
import dns.reversename

from app.config import settings

_RESOLVER = dns.resolver.Resolver()
_RESOLVER.lifetime = 5.0
# On ne se fie PAS au résolveur du conteneur. Celui de Docker (127.0.0.11) ne relaie pas
# les requêtes PTR pour les IP publiques : il répond « NoAnswer ». Le reverse DNS serait
# donc systématiquement vide — et avec lui le FCrDNS, donc toute identification
# d'expéditeur — sans qu'aucune erreur ne le signale. Vérifié, pas supposé.
_RESOLVER.nameservers = [s.strip() for s in settings.dns_resolvers.split(",") if s.strip()]

_DNS_ERRORS = (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.exception.Timeout,
               dns.resolver.NoNameservers, dns.exception.DNSException)


@dataclass
class IpFacts:
    ip: str
    ptr: str | None = None
    fcrdns: bool = False
    asn: int | None = None
    as_org: str | None = None
    country: str | None = None


def _txt(name: str) -> list[str]:
    try:
        return ["".join(p.decode() for p in r.strings)
                for r in _RESOLVER.resolve(name, "TXT")]
    except _DNS_ERRORS:
        return []


def _addresses(name: str) -> set[str]:
    out: set[str] = set()
    for rdtype in ("A", "AAAA"):
        try:
            out |= {r.address for r in _RESOLVER.resolve(name, rdtype)}
        except _DNS_ERRORS:
            continue
    return out


def _ptr(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> tuple[str | None, bool]:
    """Nom inverse, et sa validation aller-retour (FCrDNS)."""
    rev = dns.reversename.from_address(str(ip))
    try:
        answers = _RESOLVER.resolve(rev, "PTR")
    except _DNS_ERRORS:
        return None, False

    name = str(answers[0].to_text()).rstrip(".")
    if not name:
        return None, False

    # L'aller-retour : le nom annoncé doit lui-même pointer vers cette IP.
    verified = any(ipaddress.ip_address(a) == ip for a in _addresses(name))
    return name, verified


def _cymru_name(ip) -> str:
    """Le nom à interroger chez Team Cymru — dérivé du nom inverse standard.

    IPv4 : 4.3.2.1.in-addr.arpa  → 4.3.2.1.origin.asn.cymru.com
    IPv6 : ….ip6.arpa            → ….origin6.asn.cymru.com
    """
    rev = str(dns.reversename.from_address(str(ip))).rstrip(".")
    if rev.endswith(".in-addr.arpa"):
        return rev[: -len(".in-addr.arpa")] + ".origin.asn.cymru.com"
    return rev[: -len(".ip6.arpa")] + ".origin6.asn.cymru.com"


def _asn(ip) -> tuple[int | None, str | None, str | None]:
    """ASN, organisation, pays — via le service DNS public de Team Cymru.

    origin : « 15169 | 8.8.8.0/24 | US | arin | 2000-03-30 »
    asn    : « 15169 | US | arin | 2000-03-30 | GOOGLE, US »
    """
    origin = _txt(_cymru_name(ip))
    if not origin:
        return None, None, None

    fields = [f.strip() for f in origin[0].split("|")]
    if len(fields) < 3:
        return None, None, None

    # Un préfixe peut être annoncé par plusieurs AS ; on prend le premier, sans deviner.
    try:
        asn = int(fields[0].split()[0])
    except (ValueError, IndexError):
        return None, None, None
    country = fields[2] or None

    org = None
    detail = _txt(f"AS{asn}.asn.cymru.com")
    if detail:
        parts = [f.strip() for f in detail[0].split("|")]
        if len(parts) >= 5:
            org = parts[4] or None

    return asn, org, country


def lookup(ip: str) -> IpFacts:
    """Tous les faits connaissables sur une IP. N'échoue jamais."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return IpFacts(ip=ip)          # entrée invalide : aucun fait, aucune exception

    ptr, fcrdns = _ptr(addr)
    asn, as_org, country = _asn(addr)
    return IpFacts(ip=ip, ptr=ptr, fcrdns=fcrdns, asn=asn, as_org=as_org, country=country)
