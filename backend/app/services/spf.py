"""« Cette IP est-elle autorisée par le SPF de ce domaine, et par quel mécanisme ? »

C'est la question la plus utile qu'on puisse poser sur une IP rejetée, et c'est le
mécanisme — pas le verdict — qui rend l'écran actionnable : « autorisée par
include:spf.protection.outlook.com » dit à l'exploitant que le rejet vient de
l'ALIGNEMENT, pas de l'autorisation. Deux corrections opposées.

Deux principes non négociables :

 - **On n'invente jamais un verdict.** Les macros (%{…}), `ptr` et `exists` ne sont pas
   évaluables ici honnêtement. Plutôt qu'un « fail » qui ferait accuser un expéditeur
   légitime, on renvoie `indetermine` et l'écran le dit. Ne pas savoir est un état
   affichable ; se tromper avec aplomb ne l'est pas.

 - **Tout est borné.** La limite de dix requêtes DNS (RFC 7208 §4.6.4) est autant la
   conformité que la protection : sans elle, une chaîne d'`include:` hostile — ou
   simplement circulaire — nous ferait tourner en rond à chaque affichage d'écran.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass

import dns.exception
import dns.resolver

from app.config import settings

_RESOLVER = dns.resolver.Resolver()
_RESOLVER.lifetime = 5.0
# Mêmes serveurs que l'enrichissement (voir app/services/ip_intel.py) : on ne dépend pas
# du résolveur du conteneur.
_RESOLVER.nameservers = [s.strip() for s in settings.dns_resolvers.split(",") if s.strip()]

_DNS_ERRORS = (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.exception.Timeout,
               dns.resolver.NoNameservers, dns.exception.DNSException)

MAX_LOOKUPS = 10          # RFC 7208 §4.6.4

_QUALIFIERS = {"+": "pass", "-": "fail", "~": "softfail", "?": "neutral"}

# Ce qu'on ne sait pas évaluer sans mentir.
_UNSUPPORTED = ("ptr", "exists")


@dataclass
class SpfVerdict:
    result: str                      # pass|fail|softfail|neutral|none|permerror|indetermine
    mechanism: str | None = None     # le terme qui a tranché, tel qu'écrit dans la zone


class _Indeterminate(Exception):
    """Un terme qu'on ne sait pas évaluer honnêtement."""


class _PermError(Exception):
    """Défaut réel du domaine (trop de lookups, deux enregistrements SPF…)."""


class _Budget:
    """Les dix requêtes DNS de la RFC. Dépassement → on s'arrête, on ne boucle pas."""

    def __init__(self) -> None:
        self.used = 0

    def spend(self) -> None:
        self.used += 1
        if self.used > MAX_LOOKUPS:
            raise _PermError("plus de 10 requêtes DNS (RFC 7208 §4.6.4)")


def _txt(name: str) -> list[str]:
    try:
        return ["".join(p.decode() for p in r.strings)
                for r in _RESOLVER.resolve(name, "TXT")]
    except _DNS_ERRORS:
        return []


def _addresses(name: str) -> list[str]:
    out: list[str] = []
    for rdtype in ("A", "AAAA"):
        try:
            out += [r.address for r in _RESOLVER.resolve(name, rdtype)]
        except _DNS_ERRORS:
            continue
    return out


def _record(domain: str) -> str | None:
    """L'enregistrement SPF du domaine. Deux → permerror (RFC 7208 §4.5)."""
    found = [t for t in _txt(domain) if t.lower().startswith("v=spf1")]
    if len(found) > 1:
        raise _PermError("plusieurs enregistrements SPF publiés")
    return found[0] if found else None


def _in(ip, network: str) -> bool:
    try:
        return ip in ipaddress.ip_network(network, strict=False)
    except ValueError:
        raise _PermError(f"réseau illisible : {network}") from None


def _matches(term: str, ip, domain: str, budget: _Budget, seen: set[str]) -> bool:
    """Le terme (sans qualificateur) couvre-t-il l'IP ?"""
    name, _, arg = term.partition(":")
    name = name.lower()

    if "%" in term:                       # macro : non évaluable honnêtement
        raise _Indeterminate(term)
    if name in _UNSUPPORTED:
        raise _Indeterminate(term)

    if name == "all":
        return True

    if name in ("ip4", "ip6"):
        return _in(ip, arg)

    if name == "include":
        budget.spend()
        return _evaluate(arg, ip, budget, seen) == "pass"

    if name in ("a", "mx"):
        budget.spend()
        # « a » / « mx » sans argument portent sur le domaine courant.
        target, _, cidr = arg.partition("/")
        target = target or domain

        if name == "a":
            hosts = _addresses(target)
        else:
            try:
                hosts = [h for r in _RESOLVER.resolve(target, "MX")
                         for h in _addresses(str(r.exchange).rstrip("."))]
            except _DNS_ERRORS:
                hosts = []

        for h in hosts:
            addr = ipaddress.ip_address(h)
            if addr.version != ip.version:
                continue
            if cidr:
                if _in(ip, f"{h}/{cidr}"):
                    return True
            elif addr == ip:
                return True
        return False

    raise _Indeterminate(term)            # terme inconnu : on ne suppose rien


def _evaluate(domain: str, ip, budget: _Budget, seen: set[str]) -> str:
    """Le verdict SPF de `domain` pour `ip`. Renvoie un résultat, ou lève."""
    if domain in seen:                    # include: circulaire
        raise _PermError(f"boucle include: sur {domain}")
    seen = seen | {domain}

    record = _record(domain)
    if record is None:
        return "none"

    redirect: str | None = None

    for term in record.split()[1:]:       # on saute « v=spf1 »
        if term.lower().startswith("redirect="):
            redirect = term.split("=", 1)[1]
            continue
        if term.lower().startswith("exp="):
            continue                      # explication textuelle : sans effet sur le verdict

        qualifier = "+"
        if term[0] in _QUALIFIERS:
            qualifier, term = term[0], term[1:]

        if _matches(term, ip, domain, budget, seen):
            return _QUALIFIERS[qualifier]

    if redirect:
        budget.spend()
        result = _evaluate(redirect, ip, budget, seen)
        return "permerror" if result == "none" else result

    return "neutral"                      # SPF publié, aucun terme ne matche, pas de « all »


def covers(domain: str, ip: str) -> SpfVerdict:
    """Point d'entrée. N'échoue jamais : tout est traduit en verdict affichable."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return SpfVerdict(result="indetermine")

    budget = _Budget()

    # On refait la boucle de premier niveau ici pour pouvoir NOMMER le terme qui a
    # tranché — `_evaluate` renvoie un verdict, pas le mécanisme, et c'est le mécanisme
    # tel qu'écrit dans la zone du client qui lui parle.
    try:
        record = _record(domain)
        if record is None:
            return SpfVerdict(result="none")

        for term in record.split()[1:]:
            if term.lower().startswith(("redirect=", "exp=")):
                continue
            qualifier, bare = "+", term
            if term[0] in _QUALIFIERS:
                qualifier, bare = term[0], term[1:]
            if _matches(bare, addr, domain, budget, {domain}):
                return SpfVerdict(result=_QUALIFIERS[qualifier], mechanism=term)

        result = _evaluate(domain, addr, _Budget(), set())
        return SpfVerdict(result=result)

    except _Indeterminate:
        return SpfVerdict(result="indetermine")
    except _PermError:
        return SpfVerdict(result="permerror")
