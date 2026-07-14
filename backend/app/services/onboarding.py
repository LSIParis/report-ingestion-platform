"""Procédure de mise en conformité d'un domaine, vérifiée en direct.

Un runbook qu'on va relire ailleurs est un runbook qu'on oublie. Celui-ci vit dans
l'application, et il ne se contente pas d'énumérer : il **interroge le DNS** et dit ce
qui est fait, ce qui manque, et ce qui est faux.

Les fautes réellement commises pendant la mise en place — et que ces contrôles
attrapent : un `V=DMARC1` majuscule (la casse est normative en TLS-RPT), un
`tls-rpt.lsi@lsi-paris.tech` doublement fautif, un enregistrement `_mta-sts` annonçant
une politique jamais servie. Aucune de ces erreurs ne produit d'alerte : elles se
traduisent seulement par des rapports qui n'arrivent jamais.
"""
from __future__ import annotations

import re
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Literal

import dns.exception
import dns.resolver

Status = Literal["ok", "todo", "warn", "unknown"]

M365_SUFFIX = ".mail.protection.outlook.com"
_RESOLVER = dns.resolver.Resolver()
_RESOLVER.lifetime = 5.0


@dataclass
class Step:
    key: str
    title: str
    why: str
    zone: str                     # dans quelle zone DNS poser l'enregistrement
    status: Status = "todo"
    detail: str = ""
    record: dict | None = None    # {type, name, value} à créer
    found: str | None = None      # ce qui est réellement publié


@dataclass
class Checklist:
    domain: str
    mx: list[str] = field(default_factory=list)
    mx_policy: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"domain": self.domain, "mx": self.mx, "mx_policy": self.mx_policy,
                "steps": [asdict(s) for s in self.steps]}


# --------------------------------------------------------------------- DNS
def _txt(name: str) -> list[str]:
    try:
        answers = _RESOLVER.resolve(name, "TXT")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.exception.Timeout,
            dns.resolver.NoNameservers):
        return []
    return ["".join(p.decode() for p in r.strings) for r in answers]


def _has_a(name: str) -> list[str]:
    try:
        return [r.address for r in _RESOLVER.resolve(name, "A")]
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.exception.Timeout,
            dns.resolver.NoNameservers):
        return []


def resolve_mx(domain: str) -> list[str]:
    try:
        return sorted(str(r.exchange).rstrip(".").lower()
                      for r in _RESOLVER.resolve(domain, "MX"))
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.exception.Timeout,
            dns.resolver.NoNameservers):
        return []


def mx_policy_for(mx: list[str]) -> list[str]:
    """Ce qu'il faut écrire dans `mx:` — doit correspondre au CERTIFICAT du MX, pas
    seulement à son nom. Sur Microsoft 365, le certificat porte le joker."""
    if mx and all(m.endswith(M365_SUFFIX) for m in mx):
        return ["*" + M365_SUFFIX]
    return mx


# ------------------------------------------------------------------- policy
def _served_policy(domain: str) -> tuple[Status, str]:
    url = f"https://mta-sts.{domain}/.well-known/mta-sts.txt"
    try:
        with urllib.request.urlopen(url, timeout=8) as r:      # noqa: S310
            body = r.read(4096).decode("utf-8", "replace")
            ctype = r.headers.get("Content-Type", "")
    except Exception as exc:  # noqa: BLE001 — réseau, TLS, 404… tout est un échec ici
        return "todo", f"politique injoignable : {exc}"

    if "text/plain" not in ctype:
        # Une politique servie avec le mauvais Content-Type est ignorée en silence.
        return "warn", f"servie mais Content-Type={ctype!r} (attendu text/plain)"
    mode = re.search(r"^mode:\s*(\S+)", body, re.M)
    mx = re.findall(r"^mx:\s*(\S+)", body, re.M)
    return "ok", f"servie — mode={mode.group(1) if mode else '?'}, mx={', '.join(mx)}"


# ---------------------------------------------------------------- checklist
def _auth_step(key: str, *, domain: str, reporting_domain: str, prefix: str,
               expected: str, title: str, why: str, case_note: str = "") -> Step:
    """Autorisation de collecte externe (RFC 7489 §7.1 / RFC 8460 §3).

    Elle n'a de sens que si la boîte de collecte est sur un AUTRE domaine que celui
    surveillé. Quand le domaine héberge lui-même la boîte — c'est le cas du domaine de
    messagerie de la plateforme — la destination n'est pas externe : il n'y a personne
    à qui demander l'autorisation, et réclamer l'enregistrement serait faux.
    """
    if domain == reporting_domain:
        return Step(key=key, title=title, zone=domain, status="ok",
                    why=why, detail="sans objet : la boîte de collecte est sur ce "
                                    "domaine, la destination n'est pas externe")

    name = f"{domain}._report.{prefix}"
    found = next((t for t in _txt(f"{name}.{reporting_domain}")
                  if expected.split("=")[1][:6].upper() in t.upper()), None)
    if found == expected:
        status, detail = "ok", ""
    elif found:
        status, detail = "warn", f"valeur incorrecte (attendu exactement « {expected} »)"
        detail += case_note
    else:
        status, detail = "todo", ""
    return Step(key=key, title=title, why=why, zone=reporting_domain,
                status=status, detail=detail, found=found,
                record={"type": "TXT", "name": name, "value": expected})


def build(domain: str, *, mailbox: str, tlsrpt_mailbox: str, reporting_domain: str,
          mta_sts_ip: str) -> Checklist:
    domain = domain.lower()
    mx = resolve_mx(domain)
    policy_mx = mx_policy_for(mx)
    cl = Checklist(domain=domain, mx=mx, mx_policy=policy_mx)

    # 1. DMARC — sans lui, aucun rapport n'existe.
    want_rua = f"rua=mailto:{mailbox}"
    dmarc = next((t for t in _txt(f"_dmarc.{domain}") if t.startswith("v=DMARC1")), None)
    if not dmarc:
        st, detail = "todo", ""
    elif want_rua not in dmarc.replace(" ", ""):
        st, detail = "warn", "publié, mais les rapports ne vont PAS vers cette plateforme"
    else:
        st, detail = "ok", ""
    cl.steps.append(Step(
        key="dmarc", zone=domain, status=st, detail=detail, found=dmarc,
        title="Publier l'enregistrement DMARC",
        why="Sans lui, aucun rapport n'est jamais généré. Commencez en p=none : un "
            "p=reject sur un domaine jamais audité fait disparaître du courrier légitime.",
        record={"type": "TXT", "name": "_dmarc",
                "value": f"v=DMARC1; p=none; {want_rua}; adkim=s;"}))

    # 2. Autorisation DMARC.
    cl.steps.append(_auth_step(
        "dmarc_auth", domain=domain, reporting_domain=reporting_domain,
        prefix="_dmarc", expected="v=DMARC1",
        title="Autoriser la collecte des rapports DMARC",
        why="Quand la boîte de collecte n'appartient pas au domaine surveillé, notre "
            "domaine doit déclarer qu'il accepte ses rapports (RFC 7489 §7.1)."))

    # 3. TLS-RPT — l'instrument sans lequel MTA-STS est aveugle.
    #    ATTENTION : les rapports TLS vont dans une boîte DISTINCTE de celle des rapports
    #    DMARC. Ce sont deux flux différents ; les confondre publie un enregistrement qui
    #    a l'air correct et envoie les rapports au mauvais endroit.
    tlsrpt = next((t for t in _txt(f"_smtp._tls.{domain}") if t.upper().startswith("V=TLSRPT")),
                  None)
    ok_tls = bool(tlsrpt) and f"rua=mailto:{tlsrpt_mailbox}" in (tlsrpt or "").replace(" ", "")
    cl.steps.append(Step(
        key="tlsrpt", zone=domain, found=tlsrpt,
        status="ok" if ok_tls else ("warn" if tlsrpt else "todo"),
        detail="" if ok_tls else (
            "publié, mais les rapports TLS ne vont pas vers cette plateforme"
            if tlsrpt else ""),
        title="Publier l'enregistrement TLS-RPT",
        why="Il signale les échecs de chiffrement SANS bloquer le courrier. C'est lui "
            "qui rend le passage de MTA-STS en enforce sûr — sans lui, on durcit à "
            "l'aveugle.",
        record={"type": "TXT", "name": "_smtp._tls",
                "value": f"v=TLSRPTv1; rua=mailto:{tlsrpt_mailbox}"}))

    # 4. Autorisation TLS-RPT.
    cl.steps.append(_auth_step(
        "tlsrpt_auth", domain=domain, reporting_domain=reporting_domain,
        prefix="_smtp._tls", expected="v=TLSRPTv1",
        title="Autoriser la collecte des rapports TLS",
        why="Même principe que pour DMARC (RFC 8460 §3).",
        case_note=" — la casse est NORMATIVE ici : la RFC 8460 définit la version en "
                  "octets hexadécimaux, « v=TLSRPTV1 » est invalide."))

    # 5. Hôte servant la politique MTA-STS.
    ips = _has_a(f"mta-sts.{domain}")
    cl.steps.append(Step(
        key="mta_sts_host", zone=domain, found=", ".join(ips) or None,
        status="ok" if mta_sts_ip in ips else ("warn" if ips else "todo"),
        detail="" if mta_sts_ip in ips else (
            f"pointe sur {', '.join(ips)} au lieu de {mta_sts_ip}" if ips else ""),
        title="Faire pointer l'hôte de la politique MTA-STS",
        why="C'est le serveur qui sert le fichier de politique. En DNS-only : un proxy "
            "(nuage orange Cloudflare) casserait la validation du certificat.",
        record={"type": "A", "name": "mta-sts", "value": mta_sts_ip}))

    # 6. La politique elle-même, réellement servie en HTTPS.
    st, detail = _served_policy(domain)
    cl.steps.append(Step(
        key="mta_sts_policy", zone="plateforme", status=st, detail=detail,
        title="Servir la politique MTA-STS",
        why=(f"Ajouter infra/mta-sts/policies/{domain}.txt (mode: testing, "
             f"mx: {', '.join(policy_mx) or '<le MX du domaine>'}), pousser, puis "
             "redéployer la stack mta-sts. Créer ensuite dans NPM le Proxy Host "
             f"mta-sts.{domain} → http://mta-sts:80 avec certificat Let's Encrypt, "
             "SANS « Force SSL » : une redirection invaliderait la récupération.")))

    # 7. L'annonce DNS de cette politique.
    sts = next((t for t in _txt(f"_mta-sts.{domain}") if t.startswith("v=STSv1")), None)
    cl.steps.append(Step(
        key="mta_sts_txt", zone=domain, found=sts,
        status="ok" if sts else "todo",
        title="Annoncer la politique MTA-STS",
        why="L'id doit CHANGER à chaque modification de la politique, sinon les "
            "expéditeurs gardent l'ancienne en cache jusqu'à expiration de max_age.",
        record={"type": "TXT", "name": "_mta-sts", "value": "v=STSv1; id=<horodatage>"}))

    return cl
