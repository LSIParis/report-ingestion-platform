"""Sélection du canal d'alerte selon `ALERT_CHANNEL`.

Ajouter un canal = un fichier ici + une entrée dans `_CANAUX`. Le reste du système
d'alertes n'en sait rien : il appelle `get_channel().envoyer(...)`.
"""
from __future__ import annotations

from app.config import settings
from app.services.alerting.channels import desk365, webhook

_CANAUX = {"webhook": webhook, "desk365": desk365}


def get_channel():
    """Le module de canal correspondant à `settings.alert_channel`.

    Un nom inconnu lève : on ne se rabat JAMAIS en silence sur un canal par défaut — un
    canal muet choisi par erreur reproduirait la panne que ce produit combat.
    """
    canal = _CANAUX.get(settings.alert_channel)
    if canal is None:
        raise ValueError(
            f"ALERT_CHANNEL inconnu : {settings.alert_channel!r} "
            f"(attendu : {', '.join(sorted(_CANAUX))})")
    return canal
