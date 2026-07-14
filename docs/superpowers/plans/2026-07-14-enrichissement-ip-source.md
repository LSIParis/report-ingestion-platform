# Enrichissement des IP sources — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rendre une IP source rejetée d'un rapport DMARC compréhensible et actionnable : qui est-ce, mon SPF l'autorise-t-il, que dois-je faire.

**Architecture:** Quatre signaux DNS indépendants et dégradables (PTR/FCrDNS, ASN via Team Cymru, couverture SPF, catalogue local d'expéditeurs), calculés **à la demande** depuis un panneau latéral, avec un cache `ip_intel` sans `tenant_id` protégé par un contrôle d'appartenance sous RLS.

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy 2.0 · Alembic · PostgreSQL 16 (RLS) · dnspython 2.7 (déjà en dépendance) · React 19 + TanStack Query + Tailwind.

**Spec:** `docs/superpowers/specs/2026-07-14-enrichissement-ip-source-design.md`

## Global Constraints

- **Aucune nouvelle dépendance Python.** `dnspython==2.7.0` est déjà dans `backend/pyproject.toml`. Ne pas ajouter `pyspf`, `checkdmarc`, ni aucun client HTTP tiers.
- **Aucune API tierce, aucune clé.** Tous les signaux passent par le DNS. Pas de DNSBL, pas de MaxMind.
- **Toute résolution DNS est bornée** : `resolver.lifetime = 5.0`, comme dans `app/services/onboarding.py:26-27`.
- **Aucun signal ne fait échouer la requête.** Timeout, NXDOMAIN, réponse illisible → « inconnu ». Jamais de 5xx.
- **On ne devine jamais** (CLAUDE.md §6). Un SPF non évaluable renvoie `indetermine`, jamais un `fail` inventé.
- **Routes tenant : aucun `WHERE tenant_id` applicatif.** On passe par `get_db` (session déjà scopée) — la RLS fait le travail.
- **Clés canoniques des lignes DMARC** (après normalisation par `profiles/_default_dmarc_xml.json`, ce sont ces noms-là qui sont en base, pas ceux du XML) : `source_ip`, `message_count` (int), `disposition`, `spf`, `dkim`, `aligned`, `header_from`, `envelope_from`, `auth_spf`, `auth_dkim`, `report_date`, `reporter`, `policy_domain`.
- **Le test d'isolation bloque le merge.** Toute route ajoutée y est couverte.
- Commentaires et messages de commit en français, comme le reste du dépôt.

## Structure des fichiers

| Fichier | Responsabilité |
|---|---|
| `backend/app/services/ip_intel.py` | **Créer.** Les faits DNS sur une IP : PTR + FCrDNS, ASN/org/pays (Team Cymru). Rien d'autre. |
| `backend/app/services/spf.py` | **Créer.** Évaluateur SPF borné : « cette IP est-elle autorisée par le SPF de ce domaine, et par quel mécanisme ». Isolé car c'est la seule brique avec une logique normative (RFC 7208) et une limite de sécurité. |
| `backend/app/services/senders.py` | **Créer.** Chargement du catalogue + appariement, avec le garde-fou FCrDNS. |
| `backend/senders/*.json` | **Créer.** Le catalogue lui-même. Une donnée, pas du code. |
| `backend/app/db/models.py` | **Modifier.** Ajouter le modèle `IpIntel`. |
| `backend/migrations/versions/0004_ip_intel.py` | **Créer.** Table `ip_intel` (sans RLS, GRANT explicites) + index sur `report_row`. |
| `backend/app/api/ip_intel.py` | **Créer.** La route : contrôle d'appartenance sous RLS → cache → DNS → résumé d'activité. |
| `backend/app/api/schemas.py` | **Modifier.** Schémas de sortie. |
| `backend/app/main.py` | **Modifier.** Enregistrer le routeur. |
| `frontend/src/api/ipIntel.ts` | **Créer.** Hooks TanStack Query. |
| `frontend/src/components/IpPanel.tsx` | **Créer.** Le panneau latéral : verdict, preuves, action. |
| `frontend/src/pages/ReportDetail.tsx` | **Modifier.** Rendu dédié des lignes DMARC, IP cliquable. |

Les tâches 1, 2 et 3 sont **indépendantes** (pur calcul, testable hors base). La tâche 4 (schéma) conditionne la 5 (route), qui conditionne la 6 (écran).

---

### Task 1: Les faits DNS — PTR, FCrDNS, ASN

**Files:**
- Create: `backend/app/services/ip_intel.py`
- Test: `backend/tests/test_ip_intel.py`

**Interfaces:**
- Consumes: rien.
- Produces:
  - `@dataclass IpFacts(ip: str, ptr: str | None, fcrdns: bool, asn: int | None, as_org: str | None, country: str | None)`
  - `lookup(ip: str) -> IpFacts` — n'échoue jamais ; les champs inconnus valent `None` / `False`.

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `backend/tests/test_ip_intel.py` :

```python
"""Les faits DNS sur une IP. Résolveur moqué : ces tests ne touchent pas le réseau.

Le test qui compte vraiment est `test_ptr_menteur_nest_pas_verifie` : un PTR se pose
librement sur sa propre IP. Sans l'aller-retour, n'importe qui se déclare Google.
"""
import dns.resolver
import pytest

from app.services import ip_intel


class _Txt:
    def __init__(self, text: str):
        self.strings = [text.encode()]


class _A:
    def __init__(self, address: str):
        self.address = address


class _Ptr:
    def __init__(self, target: str):
        self.target = target

    def to_text(self) -> str:
        return self.target


@pytest.fixture
def dns_stub(monkeypatch):
    """Table de réponses : (nom, type) -> liste d'enregistrements, ou une exception."""
    answers: dict[tuple[str, str], object] = {}

    def fake_resolve(name, rdtype):
        key = (str(name).rstrip("."), rdtype)
        if key not in answers:
            raise dns.resolver.NXDOMAIN()
        val = answers[key]
        if isinstance(val, Exception):
            raise val
        return val

    monkeypatch.setattr(ip_intel._RESOLVER, "resolve", fake_resolve)
    return answers


def test_ptr_verifie_par_aller_retour(dns_stub):
    dns_stub[("4.3.2.1.in-addr.arpa", "PTR")] = [_Ptr("mail.exemple.net.")]
    dns_stub[("mail.exemple.net", "A")] = [_A("1.2.3.4")]

    facts = ip_intel.lookup("1.2.3.4")

    assert facts.ptr == "mail.exemple.net"
    assert facts.fcrdns is True


def test_ptr_menteur_nest_pas_verifie(dns_stub):
    # Le PTR annonce Google ; mais le nom, réinterrogé, ne redonne PAS cette IP.
    dns_stub[("4.3.2.1.in-addr.arpa", "PTR")] = [_Ptr("mail.google.com.")]
    dns_stub[("mail.google.com", "A")] = [_A("142.250.1.1")]

    facts = ip_intel.lookup("1.2.3.4")

    assert facts.ptr == "mail.google.com"
    assert facts.fcrdns is False


def test_sans_ptr(dns_stub):
    facts = ip_intel.lookup("1.2.3.4")
    assert facts.ptr is None
    assert facts.fcrdns is False


def test_asn_et_organisation_ipv4(dns_stub):
    dns_stub[("4.3.2.1.origin.asn.cymru.com", "TXT")] = [
        _Txt("15169 | 8.8.8.0/24 | US | arin | 2000-03-30")]
    dns_stub[("AS15169.asn.cymru.com", "TXT")] = [
        _Txt("15169 | US | arin | 2000-03-30 | GOOGLE, US")]

    facts = ip_intel.lookup("1.2.3.4")

    assert facts.asn == 15169
    assert facts.as_org == "GOOGLE, US"
    assert facts.country == "US"


def test_asn_ipv6_passe_par_origin6(dns_stub):
    name = ("1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0"
            ".8.b.d.0.1.0.0.2.origin6.asn.cymru.com")
    dns_stub[(name, "TXT")] = [_Txt("64500 | 2001:db8::/32 | FR | ripencc | 2010-01-01")]
    dns_stub[("AS64500.asn.cymru.com", "TXT")] = [
        _Txt("64500 | FR | ripencc | 2010-01-01 | EXEMPLE-AS, FR")]

    facts = ip_intel.lookup("2001:db8::1")

    assert facts.asn == 64500
    assert facts.country == "FR"
    assert facts.as_org == "EXEMPLE-AS, FR"


def test_timeout_ne_leve_pas_et_ne_ment_pas(dns_stub):
    dns_stub[("4.3.2.1.in-addr.arpa", "PTR")] = dns.exception.Timeout()
    dns_stub[("4.3.2.1.origin.asn.cymru.com", "TXT")] = dns.exception.Timeout()

    facts = ip_intel.lookup("1.2.3.4")

    assert facts.ptr is None and facts.asn is None
    assert facts.fcrdns is False


def test_ip_invalide_ne_leve_pas(dns_stub):
    facts = ip_intel.lookup("pas-une-ip")
    assert facts == ip_intel.IpFacts(ip="pas-une-ip")


def test_cymru_multi_asn_prend_le_premier(dns_stub):
    # Un préfixe peut être annoncé par plusieurs AS. On prend le premier, sans deviner.
    dns_stub[("4.3.2.1.origin.asn.cymru.com", "TXT")] = [
        _Txt("64500 64501 | 1.2.3.0/24 | FR | ripencc | 2010-01-01")]
    dns_stub[("AS64500.asn.cymru.com", "TXT")] = [
        _Txt("64500 | FR | ripencc | 2010-01-01 | PREMIER-AS, FR")]

    facts = ip_intel.lookup("1.2.3.4")

    assert facts.asn == 64500
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd infra && docker compose exec api pytest tests/test_ip_intel.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.ip_intel'`

- [ ] **Step 3: Écrire l'implémentation**

Créer `backend/app/services/ip_intel.py` :

```python
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

_RESOLVER = dns.resolver.Resolver()
_RESOLVER.lifetime = 5.0

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
    """ASN, pays, organisation — via le service DNS public de Team Cymru.

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
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `cd infra && docker compose exec api pytest tests/test_ip_intel.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Lint**

Run: `cd infra && docker compose exec api ruff check app/services/ip_intel.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/ip_intel.py backend/tests/test_ip_intel.py
git commit -m "feat(ip-intel): les faits DNS d une IP source — PTR, FCrDNS, ASN

Le PTR seul ne prouve rien : son proprietaire y ecrit ce qu il veut, y compris
mail.google.com. Seul l aller-retour distingue un hebergeur d un menteur — d ou
fcrdns, sur lequel tout le reste s appuiera.

L ASN vient du service DNS public de Team Cymru : gratuit, sans cle, et rien de
nous ne sort."
```

---

### Task 2: L'évaluateur SPF — honnête plutôt que malin

**Files:**
- Create: `backend/app/services/spf.py`
- Test: `backend/tests/test_spf.py`

**Interfaces:**
- Consumes: rien (résolveur DNS propre au module).
- Produces:
  - `@dataclass SpfVerdict(result: str, mechanism: str | None = None)` où `result ∈ {"pass","fail","softfail","neutral","none","permerror","indetermine"}`
  - `covers(domain: str, ip: str) -> SpfVerdict`

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `backend/tests/test_spf.py` :

```python
"""L'évaluateur SPF. Résolveur moqué : aucun accès réseau.

Deux tests portent tout le poids :
 - `test_macro_rend_indetermine` : on préfère avouer qu'on ne sait pas plutôt que
   d'inventer un « fail » qui ferait accuser un expéditeur légitime.
 - `test_limite_dix_lookups` : c'est la RFC, et c'est aussi notre protection contre une
   chaîne d'include: hostile.
"""
import dns.resolver
import pytest

from app.services import spf


class _Txt:
    def __init__(self, text: str):
        self.strings = [text.encode()]


class _A:
    def __init__(self, address: str):
        self.address = address


class _Mx:
    def __init__(self, exchange: str):
        self.exchange = exchange

    def to_text(self) -> str:
        return self.exchange


@pytest.fixture
def dns_stub(monkeypatch):
    answers: dict[tuple[str, str], object] = {}

    def fake_resolve(name, rdtype):
        key = (str(name).rstrip("."), rdtype)
        if key not in answers:
            raise dns.resolver.NXDOMAIN()
        return answers[key]

    monkeypatch.setattr(spf._RESOLVER, "resolve", fake_resolve)
    return answers


def test_ip4_directement_autorisee(dns_stub):
    dns_stub[("exemple.fr", "TXT")] = [_Txt("v=spf1 ip4:1.2.3.0/24 -all")]

    v = spf.covers("exemple.fr", "1.2.3.4")

    assert v.result == "pass"
    assert v.mechanism == "ip4:1.2.3.0/24"


def test_ip_non_couverte_donne_fail_avec_le_all(dns_stub):
    dns_stub[("exemple.fr", "TXT")] = [_Txt("v=spf1 ip4:9.9.9.0/24 -all")]

    v = spf.covers("exemple.fr", "1.2.3.4")

    assert v.result == "fail"
    assert v.mechanism == "-all"


def test_softfail(dns_stub):
    dns_stub[("exemple.fr", "TXT")] = [_Txt("v=spf1 ip4:9.9.9.0/24 ~all")]
    assert spf.covers("exemple.fr", "1.2.3.4").result == "softfail"


def test_include_trouve_le_mecanisme_reel(dns_stub):
    dns_stub[("exemple.fr", "TXT")] = [_Txt("v=spf1 include:spf.routeur.net -all")]
    dns_stub[("spf.routeur.net", "TXT")] = [_Txt("v=spf1 ip4:1.2.3.0/24 -all")]

    v = spf.covers("exemple.fr", "1.2.3.4")

    assert v.result == "pass"
    # On nomme l'include, pas le mécanisme interne : c'est l'include que l'exploitant
    # a écrit dans SA zone, et c'est donc lui qui lui parle.
    assert v.mechanism == "include:spf.routeur.net"


def test_mecanisme_a_et_mx(dns_stub):
    dns_stub[("exemple.fr", "TXT")] = [_Txt("v=spf1 a mx -all")]
    dns_stub[("exemple.fr", "A")] = [_A("5.5.5.5")]
    dns_stub[("exemple.fr", "MX")] = [_Mx("mx1.exemple.fr.")]
    dns_stub[("mx1.exemple.fr", "A")] = [_A("1.2.3.4")]

    v = spf.covers("exemple.fr", "1.2.3.4")

    assert v.result == "pass"
    assert v.mechanism == "mx"


def test_redirect(dns_stub):
    dns_stub[("exemple.fr", "TXT")] = [_Txt("v=spf1 redirect=autre.fr")]
    dns_stub[("autre.fr", "TXT")] = [_Txt("v=spf1 ip4:1.2.3.4/32 -all")]

    assert spf.covers("exemple.fr", "1.2.3.4").result == "pass"


def test_ip6(dns_stub):
    dns_stub[("exemple.fr", "TXT")] = [_Txt("v=spf1 ip6:2001:db8::/32 -all")]

    v = spf.covers("exemple.fr", "2001:db8::1")

    assert v.result == "pass"
    assert v.mechanism == "ip6:2001:db8::/32"


def test_aucun_spf_publie(dns_stub):
    assert spf.covers("exemple.fr", "1.2.3.4").result == "none"


def test_macro_rend_indetermine(dns_stub):
    # RFC 7208 §7 : les macros. On ne sait pas les évaluer — on le DIT, on n'invente pas.
    dns_stub[("exemple.fr", "TXT")] = [_Txt("v=spf1 exists:%{i}._spf.exemple.fr -all")]

    v = spf.covers("exemple.fr", "1.2.3.4")

    assert v.result == "indetermine"


def test_mecanisme_ptr_rend_indetermine(dns_stub):
    dns_stub[("exemple.fr", "TXT")] = [_Txt("v=spf1 ptr -all")]
    assert spf.covers("exemple.fr", "1.2.3.4").result == "indetermine"


def test_limite_dix_lookups(dns_stub):
    # Onze include: en chaîne. La RFC en autorise dix. Au-delà : permerror, et surtout
    # on s'arrête — une chaîne hostile ne doit pas nous faire tourner.
    dns_stub[("exemple.fr", "TXT")] = [
        _Txt("v=spf1 " + " ".join(f"include:c{i}.fr" for i in range(11)) + " -all")]
    for i in range(11):
        dns_stub[(f"c{i}.fr", "TXT")] = [_Txt("v=spf1 ip4:9.9.9.9/32 -all")]

    v = spf.covers("exemple.fr", "1.2.3.4")

    assert v.result == "permerror"


def test_boucle_include_ne_tourne_pas_indefiniment(dns_stub):
    dns_stub[("exemple.fr", "TXT")] = [_Txt("v=spf1 include:exemple.fr -all")]

    v = spf.covers("exemple.fr", "1.2.3.4")

    assert v.result in ("permerror", "fail")   # borné, quoi qu'il arrive


def test_deux_enregistrements_spf_est_une_erreur(dns_stub):
    # RFC 7208 §4.5 : plus d'un enregistrement SPF → permerror. C'est un vrai défaut
    # du domaine, on le dit plutôt que de choisir au hasard.
    dns_stub[("exemple.fr", "TXT")] = [_Txt("v=spf1 ip4:1.2.3.4/32 -all"),
                                       _Txt("v=spf1 -all")]

    assert spf.covers("exemple.fr", "1.2.3.4").result == "permerror"
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd infra && docker compose exec api pytest tests/test_spf.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.spf'`

- [ ] **Step 3: Écrire l'implémentation**

Créer `backend/app/services/spf.py` :

```python
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

_RESOLVER = dns.resolver.Resolver()
_RESOLVER.lifetime = 5.0

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

    if name == "ip4" or name == "ip6":
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
            if _matches(bare, addr, domain, budget, set()):
                return SpfVerdict(result=_QUALIFIERS[qualifier], mechanism=term)

        result = _evaluate(domain, addr, _Budget(), set())
        return SpfVerdict(result=result)

    except _Indeterminate:
        return SpfVerdict(result="indetermine")
    except _PermError:
        return SpfVerdict(result="permerror")
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `cd infra && docker compose exec api pytest tests/test_spf.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Lint**

Run: `cd infra && docker compose exec api ruff check app/services/spf.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/spf.py backend/tests/test_spf.py
git commit -m "feat(spf): evaluateur borne — cette IP est-elle autorisee, et par quel mecanisme

C est le MECANISME, pas le verdict, qui rend l ecran actionnable : autorisee par
include:spf.protection.outlook.com dit que le rejet vient de l alignement, pas de
l autorisation — deux corrections opposees.

Macros, ptr et exists ne sont pas evaluables honnetement : ils rendent indetermine.
On prefere avouer qu on ne sait pas plutot qu inventer un fail qui ferait accuser un
expediteur legitime.

La limite de dix requetes DNS (RFC 7208) est autant la conformite que la protection :
sans elle une chaine d include: circulaire nous ferait tourner a chaque affichage."
```

---

### Task 3: Le catalogue d'expéditeurs — et le garde-fou qui l'empêche de mentir

**Files:**
- Create: `backend/senders/microsoft365.json`, `backend/senders/google.json`, `backend/senders/sendgrid.json`, `backend/senders/brevo.json`, `backend/senders/mailjet.json`, `backend/senders/amazon_ses.json`, `backend/senders/mailchimp.json`
- Create: `backend/app/services/senders.py`
- Test: `backend/tests/test_senders.py`

**Interfaces:**
- Consumes: `IpFacts` (Task 1) — lit `.ptr`, `.fcrdns`, `.asn`, `.as_org`.
- Produces:
  - `@dataclass Sender(key: str, name: str, spf_include: str | None, remediation: str)`
  - `@dataclass Match(sender: Sender | None, hosted_by: str | None)`
  - `identify(facts) -> Match`

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `backend/tests/test_senders.py` :

```python
"""Le catalogue ne doit JAMAIS mentir.

Un catalogue naïf n'est pas inutile : il est menteur, et rassurant. C'est pire que rien.
Ces tests verrouillent les trois règles qui l'en empêchent.
"""
from app.services import senders
from app.services.ip_intel import IpFacts


def test_ptr_verifie_identifie_l_expediteur():
    facts = IpFacts(ip="1.2.3.4", ptr="o1.ptr1234.sendgrid.net", fcrdns=True)

    m = senders.identify(facts)

    assert m.sender is not None
    assert m.sender.name == "SendGrid"
    assert m.sender.spf_include == "sendgrid.net"
    assert "include:sendgrid.net" in m.sender.remediation


def test_ptr_menteur_n_identifie_personne():
    """LE test. Sans lui, un usurpateur pose un PTR .sendgrid.net sur son IP et notre
    écran le blanchit — au moment précis où il devrait alerter."""
    facts = IpFacts(ip="1.2.3.4", ptr="o1.ptr1234.sendgrid.net", fcrdns=False)

    m = senders.identify(facts)

    assert m.sender is None


def test_asn_seul_ne_nomme_pas_l_expediteur():
    """AS16509 est Amazon — mais l'essentiel de ses IP sont des EC2 quelconques, pas
    Amazon SES. « Hébergé chez » situe ; il ne conclut pas."""
    facts = IpFacts(ip="1.2.3.4", ptr="ec2-1-2-3-4.compute.amazonaws.com", fcrdns=True,
                    asn=16509, as_org="AMAZON-02, US")

    m = senders.identify(facts)

    assert m.sender is None
    assert m.hosted_by == "AMAZON-02, US"


def test_aucune_correspondance_ne_degrade_rien():
    facts = IpFacts(ip="1.2.3.4", ptr="host.inconnu.example", fcrdns=True,
                    asn=64500, as_org="INCONNU-AS, RU")

    m = senders.identify(facts)

    assert m.sender is None
    assert m.hosted_by == "INCONNU-AS, RU"


def test_sans_ptr_ni_asn():
    m = senders.identify(IpFacts(ip="1.2.3.4"))
    assert m.sender is None and m.hosted_by is None


def test_suffixe_le_plus_long_gagne():
    """Un suffixe plus spécifique doit l'emporter — on ne veut pas dépendre de l'ordre
    de lecture des fichiers."""
    facts = IpFacts(ip="1.2.3.4", ptr="mail-1.eu.mail.protection.outlook.com", fcrdns=True)

    m = senders.identify(facts)

    assert m.sender is not None
    assert m.sender.name == "Microsoft 365"


def test_le_catalogue_charge_est_coherent():
    """Chaque fichier doit porter les champs attendus — une faute de frappe dans un JSON
    ne doit pas se découvrir en production."""
    catalogue = senders.load()

    assert catalogue, "catalogue vide : les fichiers backend/senders/*.json sont-ils là ?"
    for s in catalogue:
        assert s.key and s.name and s.remediation
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd infra && docker compose exec api pytest tests/test_senders.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.senders'`

- [ ] **Step 3: Écrire le catalogue**

Créer `backend/senders/sendgrid.json` :

```json
{
  "key": "sendgrid",
  "name": "SendGrid",
  "ptr_suffixes": [".sendgrid.net"],
  "spf_include": "sendgrid.net",
  "remediation": "Ajoutez `include:sendgrid.net` à votre enregistrement SPF, puis activez la signature DKIM (Sender Authentication) dans la console SendGrid — sans DKIM, le courrier transféré perdra l'alignement."
}
```

Créer `backend/senders/microsoft365.json` :

```json
{
  "key": "microsoft365",
  "name": "Microsoft 365",
  "ptr_suffixes": [".mail.protection.outlook.com", ".outbound.protection.outlook.com"],
  "spf_include": "spf.protection.outlook.com",
  "remediation": "Ajoutez `include:spf.protection.outlook.com` à votre enregistrement SPF, et publiez les deux CNAME DKIM fournis par le centre d'administration Microsoft 365 (Sécurité → Politiques → DKIM)."
}
```

Créer `backend/senders/google.json` :

```json
{
  "key": "google",
  "name": "Google Workspace",
  "ptr_suffixes": [".google.com", ".googlemail.com"],
  "spf_include": "_spf.google.com",
  "remediation": "Ajoutez `include:_spf.google.com` à votre enregistrement SPF, et activez la signature DKIM dans la console d'administration Google (Applications → Gmail → Authentifier l'e-mail)."
}
```

Créer `backend/senders/brevo.json` :

```json
{
  "key": "brevo",
  "name": "Brevo",
  "ptr_suffixes": [".sendibm1.com", ".sendibm2.com", ".sendibm3.com", ".smtp-brevo.com"],
  "spf_include": "spf.brevo.com",
  "remediation": "Ajoutez `include:spf.brevo.com` à votre enregistrement SPF, et publiez l'enregistrement DKIM (`mail._domainkey`) fourni par Brevo dans Paramètres → Expéditeurs et domaines."
}
```

Créer `backend/senders/mailjet.json` :

```json
{
  "key": "mailjet",
  "name": "Mailjet",
  "ptr_suffixes": [".mailjet.com"],
  "spf_include": "spf.mailjet.com",
  "remediation": "Ajoutez `include:spf.mailjet.com` à votre enregistrement SPF, et publiez la clé DKIM fournie par Mailjet (Account settings → Sender domains)."
}
```

Créer `backend/senders/amazon_ses.json` :

```json
{
  "key": "amazon_ses",
  "name": "Amazon SES",
  "ptr_suffixes": [".amazonses.com"],
  "spf_include": "amazonses.com",
  "remediation": "Ajoutez `include:amazonses.com` à votre enregistrement SPF, et publiez les CNAME DKIM (Easy DKIM) générés par la console SES. Attention : cette identification repose sur le PTR `.amazonses.com` — une IP EC2 quelconque n'est PAS Amazon SES."
}
```

Créer `backend/senders/mailchimp.json` :

```json
{
  "key": "mailchimp",
  "name": "Mailchimp",
  "ptr_suffixes": [".mcsv.net", ".rsgsv.net", ".mcdlv.net"],
  "spf_include": "servers.mcsv.net",
  "remediation": "Ajoutez `include:servers.mcsv.net` à votre enregistrement SPF, et suivez la procédure d'authentification de domaine de Mailchimp pour la signature DKIM."
}
```

- [ ] **Step 4: Écrire le chargeur et l'appariement**

Créer `backend/app/services/senders.py` :

```python
"""Le catalogue d'expéditeurs connus. Une DONNÉE : un fichier JSON, aucun code, aucun
déploiement — même convention que `profiles/`.

Sa valeur n'est PAS le joli nom : le PTR dit déjà « sendgrid.net ». Elle est dans la
REMÉDIATION — quel `include:` ajouter au SPF, quoi activer côté DKIM. C'est ce qui fait
passer l'écran d'« identifié » à « corrigé ».

Trois règles l'empêchent de mentir. Elles ne sont pas des détails d'implémentation : un
catalogue naïf ne serait pas seulement inutile, il serait MENTEUR — et rassurant, ce qui
est pire que rien.

 1. **Seul un suffixe PTR avec FCrDNS vérifié nomme un expéditeur.** Sans l'aller-retour,
    n'importe qui pose un PTR `.sendgrid.net` sur son IP et se fait blanchir par notre
    propre écran, au moment précis où il devrait alerter.

 2. **Un ASN seul ne nomme jamais.** AS16509 est Amazon, mais l'écrasante majorité de ses
    IP sont des EC2 quelconques, pas Amazon SES ; AS15169 couvre Gmail, Workspace ET des
    VM GCP. On dit « hébergé chez », ce qui situe sans conclure.

 3. **Le catalogue ne contredit jamais les faits DNS.** Il se pose par-dessus. Un
    expéditeur reconnu ET non couvert par le SPF reste un échec — c'est même le cas le
    plus utile : « SendGrid, mais votre SPF ne l'autorise pas », avec le `include:` exact.

Rien n'est mis en cache ici : l'appariement se fait à la lecture. Corriger une entrée
prend donc effet immédiatement sur tout l'historique, sans purge ni rejeu. Une erreur de
catalogue se répare avec un fichier.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.services.ip_intel import IpFacts

SENDERS_DIR = Path(__file__).resolve().parents[2] / "senders"


@dataclass(frozen=True)
class Sender:
    key: str
    name: str
    spf_include: str | None
    remediation: str
    ptr_suffixes: tuple[str, ...]


@dataclass
class Match:
    sender: Sender | None = None
    hosted_by: str | None = None      # « hébergé chez » — situe, ne conclut pas


@lru_cache(maxsize=1)
def load() -> tuple[Sender, ...]:
    out: list[Sender] = []
    for path in sorted(SENDERS_DIR.glob("*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        out.append(Sender(
            key=d["key"],
            name=d["name"],
            spf_include=d.get("spf_include"),
            remediation=d["remediation"],
            ptr_suffixes=tuple(s.lower() for s in d.get("ptr_suffixes", [])),
        ))
    return tuple(out)


def identify(facts: IpFacts) -> Match:
    """Qui est cet expéditeur ? Réponse prudente, ou pas de réponse."""
    hosted_by = facts.as_org

    # Règle 1 : sans aller-retour vérifié, le PTR ne nomme personne.
    if not facts.ptr or not facts.fcrdns:
        return Match(sender=None, hosted_by=hosted_by)

    ptr = facts.ptr.lower()

    # Le suffixe le plus spécifique gagne — indépendant de l'ordre de lecture des fichiers.
    best: tuple[int, Sender] | None = None
    for sender in load():
        for suffix in sender.ptr_suffixes:
            if ptr.endswith(suffix) and (best is None or len(suffix) > best[0]):
                best = (len(suffix), sender)

    if best is None:
        return Match(sender=None, hosted_by=hosted_by)

    # Règle 2 : identifié → on ne redit pas « hébergé chez », ce serait du bruit.
    return Match(sender=best[1], hosted_by=None)
```

- [ ] **Step 5: Lancer les tests pour vérifier qu'ils passent**

Run: `cd infra && docker compose exec api pytest tests/test_senders.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Embarquer le catalogue dans l'image**

`backend/Dockerfile` copie les répertoires de données **un par un** (`COPY profiles ./profiles`, ligne 25). Sans cette étape, le catalogue est vert en test et **vide en production** — l'`lru_cache` renverrait un tuple vide et plus personne ne serait jamais identifié, sans la moindre erreur.

Dans `backend/Dockerfile`, ajouter après `COPY profiles ./profiles` :

```dockerfile
COPY senders ./senders
```

Reconstruire et vérifier que le catalogue est bien là :

```bash
cd infra && docker compose up --build -d api
docker compose exec api python -c "from app.services.senders import load; print([s.key for s in load()])"
```
Expected: `['amazon_ses', 'brevo', 'google', 'mailchimp', 'mailjet', 'microsoft365', 'sendgrid']`

- [ ] **Step 7: Lint + commit**

```bash
cd infra && docker compose exec api ruff check app/services/senders.py
```

```bash
git add backend/senders backend/Dockerfile backend/app/services/senders.py backend/tests/test_senders.py
git commit -m "feat(senders): catalogue d expediteurs — sa valeur est la remediation, pas le nom

Le PTR dit deja sendgrid.net. Ce que le catalogue apporte, c est quoi FAIRE :
quel include: ajouter au SPF, quoi activer cote DKIM.

Trois regles l empechent de mentir. La premiere porte tout : seul un suffixe PTR
AVEC FCrDNS verifie nomme un expediteur. Sans elle, un usurpateur pose un PTR
.sendgrid.net sur son IP et notre ecran le blanchit — exactement quand il devrait
alerter. La deuxieme : un ASN seul dit heberge chez, jamais c est. La troisieme :
le catalogue ne contredit jamais les faits DNS."
```

---

### Task 4: Le schéma — cache `ip_intel` et index de recherche

**Files:**
- Modify: `backend/app/db/models.py` (ajouter à la fin, après `AuditLog`)
- Create: `backend/migrations/versions/0004_ip_intel.py`
- Test: `backend/tests/test_ip_intel_schema.py`

**Interfaces:**
- Consumes: rien.
- Produces: modèle `IpIntel` avec les colonnes `ip` (str, PK), `ptr`, `fcrdns`, `asn`, `as_org`, `country`, `checked_at` (datetime tz).

- [ ] **Step 1: Écrire le test qui échoue**

Créer `backend/tests/test_ip_intel_schema.py` :

```python
"""Le cache ip_intel : accessible aux deux plans, SANS tenant_id — et c'est assumé.

Ce sont des faits publics sur Internet, pas des données de client (comme `tenant` ou
`audit_log`, déjà hors RLS dans 0002). Ce qui ferme la fuite entre clients n'est pas la
RLS sur cette table : c'est le contrôle d'appartenance de la route (Task 5), qui exige
que l'IP soit déjà visible du tenant AVANT toute lecture du cache.
"""
from datetime import datetime, timezone

from sqlalchemy import text

from app.db.models import IpIntel
from app.db.session import get_session, tenant_scoped_session


def test_le_plan_api_peut_lire_et_ecrire_le_cache():
    with tenant_scoped_session(tenant_id=None) as db:
        db.add(IpIntel(ip="203.0.113.7", ptr="test.exemple.invalid", fcrdns=False,
                       asn=64500, as_org="TEST-AS", country="FR",
                       checked_at=datetime.now(timezone.utc)))
        db.flush()
        assert db.get(IpIntel, "203.0.113.7") is not None
        db.rollback()


def test_index_de_recherche_par_ip_source_existe():
    """Sans lui, chaque ouverture du panneau ferait un seq scan sur report_row."""
    with get_session() as db:
        rows = db.execute(text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'report_row' AND indexname = 'ix_report_row_source_ip'"
        )).all()
    assert rows, "index ix_report_row_source_ip absent"


def test_ip_intel_na_pas_de_rls_et_c_est_voulu():
    with get_session() as db:
        enabled = db.execute(text(
            "SELECT relrowsecurity FROM pg_class WHERE relname = 'ip_intel'"
        )).scalar()
    assert enabled is False
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

Run: `cd infra && docker compose exec api pytest tests/test_ip_intel_schema.py -v`
Expected: FAIL — `ImportError: cannot import name 'IpIntel'`

- [ ] **Step 3: Ajouter le modèle**

Dans `backend/app/db/models.py`, ajouter à la fin du fichier :

```python
class IpIntel(Base):
    """Cache des faits DNS sur une IP. SANS tenant_id, délibérément : ce sont des faits
    publics sur Internet, pas des données de client.

    L'écart à l'invariant n°1 (CLAUDE.md) est compensé ailleurs : l'API ne lit jamais
    cette table sans avoir d'abord vérifié, SOUS RLS, que l'IP apparaît dans une ligne de
    rapport visible du tenant. Une IP jamais vue par ce tenant → 404, avant même de
    toucher au cache. Impossible donc de sonder l'existence d'une IP chez un autre client.

    L'expéditeur reconnu n'est PAS stocké ici : l'appariement avec le catalogue se fait à
    la lecture, pour qu'une correction du catalogue prenne effet sur tout l'historique
    sans purge ni rejeu.
    """
    __tablename__ = "ip_intel"
    ip: Mapped[str] = mapped_column(Text, primary_key=True)
    ptr: Mapped[str | None] = mapped_column(Text)
    fcrdns: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    asn: Mapped[int | None] = mapped_column(Integer)
    as_org: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
```

Vérifier que `Boolean` est importé en haut de `models.py` ; l'ajouter à l'import
`from sqlalchemy import ...` si absent.

- [ ] **Step 4: Écrire la migration**

Créer `backend/migrations/versions/0004_ip_intel.py` :

```python
"""Cache des faits DNS sur une IP, et l'index qui rend le panneau instantané.

`ip_intel` n'a PAS de tenant_id et n'a PAS de RLS — comme `tenant` ou `audit_log` en
0002 : ce sont des faits publics sur Internet, pas des données de client. Ce qui empêche
un client de sonder l'existence d'une IP chez un autre n'est pas une policy sur cette
table, c'est le contrôle d'appartenance de la route : elle exige que l'IP apparaisse dans
une ligne de rapport visible SOUS RLS avant de lire le cache. IP jamais vue → 404.

L'index sur report_row sert les deux usages de la route : le contrôle d'appartenance et
le résumé d'activité. Sans lui, chaque ouverture du panneau déclenche un seq scan.

Revision ID: 0004_ip_intel
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_ip_intel"
down_revision = "0003_mta_sts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ip_intel",
        sa.Column("ip", sa.Text(), primary_key=True),
        sa.Column("ptr", sa.Text()),
        sa.Column("fcrdns", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("asn", sa.Integer()),
        sa.Column("as_org", sa.Text()),
        sa.Column("country", sa.Text()),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    # Table non-tenant : GRANT explicites, pas de RLS (même traitement qu'en 0002).
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ip_intel TO app_api;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ip_intel TO app_worker;")

    op.execute("""
        CREATE INDEX ix_report_row_source_ip
          ON report_row (tenant_id, (data->>'source_ip'));
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_report_row_source_ip;")
    op.drop_table("ip_intel")
```

- [ ] **Step 5: Appliquer la migration**

Run: `cd infra && docker compose exec api alembic upgrade head`
Expected: `Running upgrade 0003_mta_sts -> 0004_ip_intel`

- [ ] **Step 6: Lancer les tests**

Run: `cd infra && docker compose exec api pytest tests/test_ip_intel_schema.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Vérifier qu'on n'a rien cassé**

Run: `cd infra && docker compose exec api pytest tests/test_tenant_isolation.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/app/db/models.py backend/migrations/versions/0004_ip_intel.py backend/tests/test_ip_intel_schema.py
git commit -m "feat(db): cache ip_intel, sans tenant_id — et l index qui va avec

Ecart assume a l invariant n1 : ce sont des faits publics sur Internet, pas des
donnees de client (comme tenant et audit_log, deja hors RLS en 0002).

Ce qui ferme la fuite entre clients n est PAS une policy sur cette table : c est le
controle d appartenance de la route, qui exige que l IP soit deja visible du tenant
sous RLS avant toute lecture du cache. IP jamais vue par ce tenant : 404."
```

---

### Task 5: La route — appartenance sous RLS, puis cache, puis DNS

**Files:**
- Create: `backend/app/api/ip_intel.py`
- Modify: `backend/app/main.py` (imports ligne 3 + `include_router`)
- Modify: `backend/tests/test_tenant_isolation.py` (ajouter le test bloquant)
- Test: `backend/tests/test_ip_intel_api.py`

**Interfaces:**
- Consumes: `ip_intel.lookup(ip) -> IpFacts` (Task 1), `spf.covers(domain, ip) -> SpfVerdict` (Task 2), `senders.identify(facts) -> Match` (Task 3), modèle `IpIntel` (Task 4).
- Produces: `GET /ip-intel/{ip}` et `POST /ip-intel/{ip}/refresh`.

**Forme exacte de la réponse** (le front de la Task 6 s'y adosse) :

```json
{
  "ip": "1.2.3.4",
  "ptr": "o1.ptr1234.sendgrid.net",
  "fcrdns": true,
  "asn": 11377,
  "as_org": "SENDGRID, US",
  "country": "US",
  "checked_at": "2026-07-14T10:00:00+00:00",
  "sender": {"key": "sendgrid", "name": "SendGrid",
             "spf_include": "sendgrid.net", "remediation": "Ajoutez …"},
  "hosted_by": null,
  "spf": {"result": "fail", "mechanism": "-all"},
  "activity": {"messages": 412, "rows": 7,
               "first_seen": "2026-06-01", "last_seen": "2026-07-13",
               "dispositions": {"none": 412}, "aligned": {"fail": 412},
               "spf_domains": ["exemple.fr"], "dkim_domains": [],
               "header_froms": ["exemple.fr"]}
}
```

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `backend/tests/test_ip_intel_api.py` :

```python
"""La route d'enrichissement. Le DNS est moqué : ces tests ne touchent pas le réseau.

Le contrôle d'appartenance est testé ici ET dans test_tenant_isolation.py (bloquant).
Il est le seul rempart entre le cache — qui n'a pas de tenant_id — et une fuite
d'existence entre clients.
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.middleware import TenantContext
from app.db.models import Email, IpIntel, Report, ReportRow, Tenant
from app.db.session import get_session
from app.services.ip_intel import IpFacts
from app.services.spf import SpfVerdict


@pytest.fixture
def tenant_avec_ligne_dmarc():
    """Un tenant, un rapport, une ligne DMARC portant l'IP 203.0.113.9."""
    with get_session() as db:
        t = Tenant(domain="ip-test.example", name="IP Test")
        db.add(t)
        db.flush()
        em = Email(tenant_id=t.id, message_id=f"ip-{uuid.uuid4()}",
                   from_address="reports@ip-test.example", subject="t",
                   received_at=datetime.now(timezone.utc),
                   raw_object_key="raw/t.eml", status="parsed_ok")
        db.add(em)
        db.flush()
        rep = Report(tenant_id=t.id, email_id=em.id, source_type="attachment", status="ok")
        db.add(rep)
        db.flush()
        db.add(ReportRow(tenant_id=t.id, report_id=rep.id, data={
            "source_ip": "203.0.113.9", "message_count": 412, "disposition": "none",
            "spf": "fail", "dkim": "fail", "aligned": "fail",
            "header_from": "ip-test.example", "auth_spf": "usurpateur.example",
            "auth_dkim": None, "report_date": "2026-07-13",
        }))
        db.commit()
        ids = (str(t.id), str(em.id), str(rep.id))

    yield ids

    with get_session() as db:
        db.query(ReportRow).filter_by(report_id=ids[2]).delete()
        db.query(Report).filter_by(id=ids[2]).delete()
        db.query(Email).filter_by(id=ids[1]).delete()
        db.query(Tenant).filter_by(id=ids[0]).delete()
        db.query(IpIntel).filter_by(ip="203.0.113.9").delete()
        db.commit()


@pytest.fixture
def client_du_tenant(tenant_avec_ligne_dmarc, monkeypatch):
    """TestClient scopé sur ce tenant, DNS moqué.

    Même montage que `tests/test_admin_domains.py` : une app neuve avec le seul routeur
    et un TenantContext injecté. `bypass=False` — le client est un vrai tenant, soumis à
    la RLS : c'est précisément ce qu'on veut éprouver ici.
    """
    from app.api import ip_intel as route

    tid = tenant_avec_ligne_dmarc[0]

    monkeypatch.setattr(route.ip_intel, "lookup", lambda ip: IpFacts(
        ip=ip, ptr="o1.ptr1234.sendgrid.net", fcrdns=True,
        asn=11377, as_org="SENDGRID, US", country="US"))
    monkeypatch.setattr(route.spf, "covers",
                        lambda domain, ip: SpfVerdict(result="fail", mechanism="-all"))

    app = FastAPI()
    ctx = TenantContext(user="viewer@ip-test.example", role="tenant_viewer",
                        tenant_ids=(tid,), active_tenant=tid, bypass=False)

    @app.middleware("http")
    async def inject_ctx(request, call_next):
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(route.router)
    yield TestClient(app), tid


def test_ip_inconnue_du_tenant_donne_404(client_du_tenant):
    client, _ = client_du_tenant

    r = client.get("/ip-intel/198.51.100.1")

    assert r.status_code == 404


def test_ip_connue_renvoie_faits_verdict_et_activite(client_du_tenant):
    client, _ = client_du_tenant

    r = client.get("/ip-intel/203.0.113.9")

    assert r.status_code == 200
    b = r.json()
    assert b["ptr"] == "o1.ptr1234.sendgrid.net"
    assert b["fcrdns"] is True
    assert b["sender"]["name"] == "SendGrid"          # PTR vérifié → identifié
    assert b["hosted_by"] is None
    assert b["spf"]["result"] == "fail"               # reconnu MAIS non autorisé
    assert b["activity"]["messages"] == 412
    assert b["activity"]["dispositions"] == {"none": 412}
    assert b["activity"]["spf_domains"] == ["usurpateur.example"]


def test_le_cache_evite_une_seconde_resolution_dns(client_du_tenant, monkeypatch):
    from app.api import ip_intel as route

    client, _ = client_du_tenant
    appels = {"n": 0}

    def compte(ip):
        appels["n"] += 1
        return IpFacts(ip=ip, ptr="x.sendgrid.net", fcrdns=True)

    monkeypatch.setattr(route.ip_intel, "lookup", compte)

    client.get("/ip-intel/203.0.113.9")
    client.get("/ip-intel/203.0.113.9")

    assert appels["n"] == 1, "le second appel aurait dû être servi par le cache"


def test_refresh_force_une_nouvelle_resolution(client_du_tenant, monkeypatch):
    from app.api import ip_intel as route

    client, _ = client_du_tenant
    appels = {"n": 0}

    def compte(ip):
        appels["n"] += 1
        return IpFacts(ip=ip, ptr="x.sendgrid.net", fcrdns=True)

    monkeypatch.setattr(route.ip_intel, "lookup", compte)

    client.get("/ip-intel/203.0.113.9")
    client.post("/ip-intel/203.0.113.9/refresh")

    assert appels["n"] == 2


def test_refresh_sur_ip_inconnue_donne_404(client_du_tenant):
    client, _ = client_du_tenant
    assert client.post("/ip-intel/198.51.100.1/refresh").status_code == 404


def test_ip_syntaxiquement_invalide_donne_400(client_du_tenant):
    client, _ = client_du_tenant
    assert client.get("/ip-intel/pas-une-ip").status_code == 400
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `cd infra && docker compose exec api pytest tests/test_ip_intel_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.api.ip_intel'`

- [ ] **Step 3: Écrire la route**

Créer `backend/app/api/ip_intel.py` :

```python
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

from app.auth.deps import get_db
from app.db.models import IpIntel, ReportRow, Tenant
from app.services import ip_intel, senders, spf

router = APIRouter(prefix="/ip-intel", tags=["ip-intel"])

# Les faits DNS bougent lentement. Une semaine suffit ; le bouton « réinterroger » est là
# pour les cas où l'exploitant vient justement de corriger quelque chose.
FRAICHEUR = timedelta(days=7)


def _rows_de_cette_ip(db, ip: str) -> list[ReportRow]:
    """Les lignes de rapport où cette IP apparaît — SOUS RLS.

    Aucun `WHERE tenant_id` applicatif : la session est déjà scopée (CLAUDE.md). Une IP
    vue par un autre tenant ne renverra rien ici, et c'est exactement le but.
    """
    return (db.query(ReportRow)
              .filter(ReportRow.data["source_ip"].astext == ip)
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

    for r in rows:
        d = r.data
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
        if d.get("report_date"):
            dates.append(str(d["report_date"]))

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
```

- [ ] **Step 4: Enregistrer le routeur**

Dans `backend/app/main.py`, modifier la ligne d'import :

```python
from app.api import admin, emails, ingest, ip_intel, metrics, mta_sts, reports
```

et ajouter, après `app.include_router(admin.router)` :

```python
app.include_router(ip_intel.router)
```

- [ ] **Step 5: Lancer les tests**

Run: `cd infra && docker compose exec api pytest tests/test_ip_intel_api.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Ajouter le test d'isolation BLOQUANT**

Ajouter à la fin de `backend/tests/test_tenant_isolation.py` :

```python
def test_ip_vue_par_b_est_invisible_de_a(seed_two_tenants):
    """Le cache ip_intel n'a pas de tenant_id. Ce qui empêche A de sonder l'existence
    d'une IP chez B, c'est le contrôle d'appartenance de la route : la requête ne trouve
    la ligne que si elle est visible SOUS RLS.

    Ce test valide la brique sur laquelle ce contrôle repose. S'il tombe, la route peut
    devenir un oracle : « cette IP est-elle dans votre cache ? » révélerait le trafic
    d'un autre client.
    """
    from app.db.models import Report, ReportRow

    tid_a, tid_b = seed_two_tenants

    with get_session() as db:                       # plan worker : on sème chez B
        rep_b = db.query(Report).filter_by(tenant_id=tid_b).first()
        db.add(ReportRow(tenant_id=tid_b, report_id=rep_b.id,
                         data={"source_ip": "198.51.100.42", "message_count": 5}))
        db.commit()

    try:
        with tenant_scoped_session(tenant_id=tid_a) as db:   # A cherche l'IP de B
            vues = (db.query(ReportRow)
                      .filter(ReportRow.data["source_ip"].astext == "198.51.100.42")
                      .all())
            assert vues == [], "A voit une ligne de B : la route deviendrait un oracle"
    finally:
        with get_session() as db:
            db.query(ReportRow).filter(
                ReportRow.data["source_ip"].astext == "198.51.100.42").delete(
                synchronize_session=False)
            db.commit()
```

- [ ] **Step 7: Lancer le test d'isolation (bloquant)**

Run: `cd infra && docker compose exec api pytest tests/test_tenant_isolation.py -v`
Expected: PASS (4 tests)

- [ ] **Step 8: Lint + suite complète**

```bash
cd infra && docker compose exec api ruff check app
cd infra && docker compose exec api pytest
```
Expected: `All checks passed!` puis la suite verte.

- [ ] **Step 9: Commit**

```bash
git add backend/app/api/ip_intel.py backend/app/main.py backend/tests/test_ip_intel_api.py backend/tests/test_tenant_isolation.py
git commit -m "feat(api): GET /ip-intel/{ip} — appartenance sous RLS, puis cache, puis DNS

L ordre n est pas negociable. Le cache n a pas de tenant_id : interroge librement,
il ferait de l API un oracle — cette IP est-elle dans votre cache ? revelerait qu un
AUTRE client l a vue passer.

Donc : l IP apparait-elle dans une ligne visible du tenant, sous RLS ? Sinon 404 —
et 404, pas 403 : un 403 confirmerait l existence. Le cache n est touche qu apres.

Le resume d activite est souvent l element decisif : 412 messages tous en echec sur
votre header_from ne se lit pas comme 3 messages."
```

---

### Task 6: L'écran — lignes DMARC lisibles et panneau latéral

**Files:**
- Create: `frontend/src/api/ipIntel.ts`
- Create: `frontend/src/components/IpPanel.tsx`
- Modify: `frontend/src/pages/ReportDetail.tsx` (remplacer `RowsTable`, lignes 57-88)

**Interfaces:**
- Consumes: `GET /ip-intel/{ip}` et `POST /ip-intel/{ip}/refresh` (Task 5), forme exacte donnée en tête de la Task 5.
- Produces: rien (feuille de l'arbre).

- [ ] **Step 1: Écrire le client d'API**

Créer `frontend/src/api/ipIntel.ts` :

```typescript
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";

export interface Sender {
  key: string;
  name: string;
  spf_include: string | null;
  remediation: string;
}

export interface IpIntel {
  ip: string;
  ptr: string | null;
  fcrdns: boolean;
  asn: number | null;
  as_org: string | null;
  country: string | null;
  checked_at: string;
  sender: Sender | null;
  hosted_by: string | null;
  spf: {
    result: "pass" | "fail" | "softfail" | "neutral" | "none" | "permerror" | "indetermine";
    mechanism: string | null;
  };
  activity: {
    messages: number;
    rows: number;
    first_seen: string | null;
    last_seen: string | null;
    dispositions: Record<string, number>;
    aligned: Record<string, number>;
    spf_domains: string[];
    dkim_domains: string[];
    header_froms: string[];
  };
}

export const useIpIntel = (ip: string | null) =>
  useQuery({
    queryKey: ["ip-intel", ip],
    queryFn: () => api<IpIntel>(`/ip-intel/${ip}`),
    enabled: ip !== null,
  });

export function useRefreshIpIntel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ip: string) => api<IpIntel>(`/ip-intel/${ip}/refresh`, { method: "POST" }),
    onSuccess: (data) => qc.setQueryData(["ip-intel", data.ip], data),
  });
}
```

- [ ] **Step 2: Écrire le panneau**

Créer `frontend/src/components/IpPanel.tsx` :

```tsx
import { type IpIntel, useIpIntel, useRefreshIpIntel } from "../api/ipIntel";

/** Le verdict, en une phrase — c'est la seule chose que beaucoup liront.
 *
 * Identification et autorisation sont INDÉPENDANTES : « SendGrid, mais votre SPF ne
 * l'autorise pas » est parfaitement cohérent, et c'est même le cas le plus utile.
 * Les confondre serait l'erreur à ne pas commettre. */
function verdict(d: IpIntel): { titre: string; ton: "danger" | "warn" | "ok" } {
  const autorise = d.spf.result === "pass";
  const nom = d.sender?.name;

  if (nom && autorise) return { titre: `${nom} — autorisée par votre SPF`, ton: "ok" };
  if (nom) return { titre: `${nom} — mais votre SPF ne l'autorise pas`, ton: "warn" };
  if (autorise) return { titre: "Expéditeur non identifié, mais autorisé par votre SPF", ton: "warn" };
  if (d.spf.result === "indetermine" || d.spf.result === "permerror")
    return { titre: "Expéditeur non identifié — SPF non évaluable", ton: "warn" };
  return { titre: "Expéditeur non identifié, autorisé par aucun mécanisme de votre SPF",
           ton: "danger" };
}

const TONS = {
  danger: "bg-red-50 border-red-200 text-red-900",
  warn: "bg-amber-50 border-amber-200 text-amber-900",
  ok: "bg-green-50 border-green-200 text-green-900",
};

const SPF_LABEL: Record<IpIntel["spf"]["result"], string> = {
  pass: "autorisée",
  fail: "refusée",
  softfail: "refusée (souple)",
  neutral: "neutre",
  none: "aucun SPF publié",
  permerror: "SPF en erreur",
  indetermine: "indéterminé",
};

export function IpPanel({ ip, onClose }: { ip: string; onClose: () => void }) {
  const { data, isLoading, error } = useIpIntel(ip);
  const refresh = useRefreshIpIntel();

  return (
    <aside className="fixed right-0 top-0 h-full w-[26rem] bg-white border-l shadow-xl
                      overflow-y-auto p-5 z-20">
      <div className="flex items-start justify-between mb-4">
        <h2 className="font-mono text-lg">{ip}</h2>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-700 text-xl">×</button>
      </div>

      {isLoading && <p className="text-gray-500">Interrogation du DNS…</p>}
      {error && <p className="text-gray-500">Cette IP n'apparaît dans aucun de vos rapports.</p>}

      {data && (
        <>
          <div className={`border rounded p-3 mb-5 ${TONS[verdict(data).ton]}`}>
            <p className="font-medium">{verdict(data).titre}</p>
          </div>

          <Section titre="Ce que dit le DNS">
            <Fait label="Reverse DNS">
              {data.ptr ? (
                <>
                  <span className="font-mono text-xs">{data.ptr}</span>
                  {data.fcrdns ? (
                    <span className="ml-2 text-xs text-green-700">✓ vérifié</span>
                  ) : (
                    // Un PTR non vérifié n'est pas un détail : c'est ce qui empêche
                    // d'identifier l'expéditeur, et c'est un signal en soi.
                    <span className="ml-2 text-xs text-red-700">✗ incohérent</span>
                  )}
                </>
              ) : (
                <span className="text-gray-500">aucun — les routeurs légitimes en ont tous un</span>
              )}
            </Fait>
            <Fait label="Réseau">
              {data.asn ? (
                <>AS{data.asn} · {data.as_org ?? "?"}{data.country ? ` · ${data.country}` : ""}</>
              ) : (
                <span className="text-gray-500">inconnu</span>
              )}
            </Fait>
            <Fait label="Votre SPF">
              {SPF_LABEL[data.spf.result]}
              {data.spf.mechanism && (
                <span className="ml-1 font-mono text-xs text-gray-500">
                  ({data.spf.mechanism})
                </span>
              )}
            </Fait>
            {data.hosted_by && (
              <Fait label="Hébergement">
                {/* « Hébergé chez » situe, ne conclut pas : un ASN ne nomme pas
                    l'expéditeur — la plupart des IP d'AWS sont des EC2 quelconques. */}
                hébergé chez {data.hosted_by}
              </Fait>
            )}
          </Section>

          <Section titre="Ce que vous avez observé">
            <Fait label="Messages">{data.activity.messages}</Fait>
            <Fait label="Période">
              {data.activity.first_seen ?? "?"} → {data.activity.last_seen ?? "?"}
            </Fait>
            <Fait label="Alignement">
              {Object.entries(data.activity.aligned)
                .map(([k, v]) => `${k} : ${v}`).join(" · ") || "—"}
            </Fait>
            <Fait label="Traitement">
              {Object.entries(data.activity.dispositions)
                .map(([k, v]) => `${k} : ${v}`).join(" · ") || "—"}
            </Fait>
            <Fait label="Domaines usurpés">
              {data.activity.header_froms.join(", ") || "—"}
            </Fait>
          </Section>

          <Section titre="Que faire">
            {data.sender ? (
              <p className="text-sm leading-relaxed">{data.sender.remediation}</p>
            ) : (
              <p className="text-sm leading-relaxed text-gray-700">
                Aucun expéditeur connu ne correspond, et votre SPF ne l'autorise pas. Si
                vous ne reconnaissez pas ce service, il n'y a rien à faire : c'est une
                usurpation, et votre politique DMARC la traite déjà. Ne l'autorisez que si
                vous identifiez un service qui vous appartient.
              </p>
            )}
          </Section>

          <button
            onClick={() => refresh.mutate(ip)}
            disabled={refresh.isPending}
            className="mt-2 text-xs text-blue-600 hover:underline disabled:opacity-40"
          >
            {refresh.isPending ? "…" : "Réinterroger le DNS"}
          </button>
          <p className="mt-1 text-xs text-gray-400">
            Faits DNS relevés le {new Date(data.checked_at).toLocaleString("fr-FR")}
          </p>
        </>
      )}
    </aside>
  );
}

function Section({ titre, children }: { titre: string; children: React.ReactNode }) {
  return (
    <section className="mb-5">
      <h3 className="text-xs uppercase tracking-wide text-gray-400 mb-2">{titre}</h3>
      <dl className="space-y-1">{children}</dl>
    </section>
  );
}

function Fait({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2 text-sm">
      <dt className="w-32 shrink-0 text-gray-500">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}
```

- [ ] **Step 3: Rendre les lignes DMARC lisibles et l'IP cliquable**

Dans `frontend/src/pages/ReportDetail.tsx` :

Ajouter les imports en tête :

```tsx
import { IpPanel } from "../components/IpPanel";
```

Remplacer entièrement la fonction `RowsTable` (lignes 57-88) par :

```tsx
function RowsTable({ reportId }: { reportId: string }) {
  const [page, setPage] = useState(1);
  const [ip, setIp] = useState<string | null>(null);
  const { data, isLoading } = useReportRows(reportId, page);
  if (isLoading) return <p>Chargement…</p>;
  const rows = data!.items;
  if (!rows.length) return <p className="text-gray-500">Aucune donnée.</p>;

  // Une ligne DMARC se reconnaît à ses DONNÉES, pas à un nom de profil : `Report` ne
  // stocke pas le format, seulement source_type (attachment/body) et profile_id.
  const isDmarc = "source_ip" in rows[0];

  return (
    <>
      {isDmarc ? (
        <DmarcTable rows={rows} onSelectIp={setIp} />
      ) : (
        <GenericTable rows={rows} />
      )}
      <div className="flex gap-2 mt-4 items-center">
        <button disabled={page <= 1} onClick={() => setPage(page - 1)} className="disabled:opacity-40">←</button>
        <span className="text-sm">Page {page} · {data?.total} lignes</span>
        <button disabled={rows.length < 50} onClick={() => setPage(page + 1)} className="disabled:opacity-40">→</button>
      </div>
      {ip && <IpPanel ip={ip} onClose={() => setIp(null)} />}
    </>
  );
}

/** Les lignes DMARC méritent mieux qu'un vidage de JSON : ce sont elles qu'on lit pour
 *  décider. L'IP est le seul point d'entrée de l'enquête — donc elle est cliquable. */
function DmarcTable({ rows, onSelectIp }: {
  rows: Record<string, unknown>[];
  onSelectIp: (ip: string) => void;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-gray-500 border-b">
          <tr>
            <th className="py-2 pr-4">IP source</th>
            <th className="py-2 pr-4">Messages</th>
            <th className="py-2 pr-4">Alignement</th>
            <th className="py-2 pr-4">Traitement</th>
            <th className="py-2 pr-4">SPF / DKIM</th>
            <th className="py-2 pr-4">De</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const aligned = String(row.aligned ?? "");
            return (
              <tr key={i} className="border-b">
                <td className="py-1 pr-4">
                  <button
                    onClick={() => onSelectIp(String(row.source_ip))}
                    className="font-mono text-blue-600 hover:underline"
                  >
                    {String(row.source_ip)}
                  </button>
                </td>
                <td className="py-1 pr-4">{String(row.message_count ?? "—")}</td>
                <td className="py-1 pr-4">
                  <span className={aligned === "pass" ? "text-green-700" : "text-red-700"}>
                    {aligned || "—"}
                  </span>
                </td>
                <td className="py-1 pr-4">{String(row.disposition ?? "—")}</td>
                <td className="py-1 pr-4 text-gray-500">
                  {String(row.spf ?? "—")} / {String(row.dkim ?? "—")}
                </td>
                <td className="py-1 pr-4">{String(row.header_from ?? "—")}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Les autres rapports (CSV, XLSX, PDF…) gardent le rendu générique : on ne connaît pas
 *  leurs colonnes à l'avance. */
function GenericTable({ rows }: { rows: Record<string, unknown>[] }) {
  const cols = Object.keys(rows[0]);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-gray-500 border-b">
          <tr>{cols.map((c) => <th key={c} className="py-2 pr-4">{c}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b">
              {cols.map((c) => <td key={c} className="py-1 pr-4">{String(row[c] ?? "—")}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 4: Vérifier la compilation TypeScript**

Run: `cd frontend && npm run build`
Expected: build réussi, aucune erreur TS.

- [ ] **Step 5: Vérifier en vrai (obligatoire — pas seulement le build)**

Lancer la stack, ouvrir un rapport DMARC, cliquer une IP rejetée. Contrôler :
- le panneau s'ouvre et affiche un verdict en une phrase ;
- le PTR porte un badge « vérifié » ou « incohérent » ;
- « Que faire » propose une remédiation concrète si l'expéditeur est reconnu ;
- « Réinterroger le DNS » met à jour la date en bas ;
- une IP d'un rapport non-DMARC ne casse rien (table générique conservée).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/ipIntel.ts frontend/src/components/IpPanel.tsx frontend/src/pages/ReportDetail.tsx
git commit -m "feat(front): une IP source rejetee devient une enquete en un clic

La table vidait le JSON brut : auth_spf et auth_dkim, que le parsing extrait deja et
qui identifient souvent l expediteur a eux seuls, y etaient noyes et illisibles.

Le panneau commence par le VERDICT — la seule chose que beaucoup liront — puis les
preuves, puis l action. Identification et autorisation restent independantes :
SendGrid, mais votre SPF ne l autorise pas est le cas le plus utile de tous."
```

---

## Vérification finale

- [ ] `cd infra && docker compose exec api pytest` — suite complète verte
- [ ] `cd infra && docker compose exec api pytest tests/test_tenant_isolation.py -v` — **bloquant**, 4 tests
- [ ] `cd infra && docker compose exec api ruff check app` — `All checks passed!`
- [ ] `cd frontend && npm run build` — build vert
- [ ] Parcours réel : ouvrir un rapport DMARC → cliquer une IP rejetée → lire le verdict → « Réinterroger le DNS »

## Ce que ce plan ne fait PAS, délibérément

- **Pas de page « Sources » agrégée par IP.** Elle répond à une autre question (« quelles sont mes IP à problème ? ») et se justifiera. Pas maintenant.
- **Pas d'enrichissement à l'ingestion.** Le pipeline ne doit pas dépendre du DNS pour aboutir, et l'historique bénéficie de l'enrichissement sans rejeu.
- **Pas de DNSBL, pas de MaxMind.** Dépendances externes payantes pour un signal moins décisif que la couverture SPF.
- **Aucun de ces signaux ne prouve une intention.** Une IP inconnue au PTR incohérent chez un hébergeur obscur est *probablement* une usurpation ; une IP Google *peut* être un compte employé compromis. Le panneau présente un faisceau, jamais une accusation. Le verdict aide à décider — il ne décide pas.
