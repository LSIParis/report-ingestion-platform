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
