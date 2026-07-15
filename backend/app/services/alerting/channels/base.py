"""Le contrat commun à tous les canaux d'alerte.

Un canal expose `envoyer(event, alert, tenant) -> bool` :
 - True  : quelque chose a ete emis ;
 - False : le canal n'est pas configure, OU l'evenement ne doit rien produire ;
 - leve `CanalIndisponible` : le canal est configure mais l'appel externe a echoue
   (Celery retentera).

`workers/tasks.py` n'attrape QUE `CanalIndisponible` : chaque canal fait donc heriter
ses propres exceptions de panne de celle-ci.
"""
from __future__ import annotations


class CanalIndisponible(Exception):
    """Le canal est configuré mais l'appel externe a échoué. Celery retentera."""
