"""Normalisation/validation de l'adresse e-mail, source UNIQUE.

L'adresse n'est qu'un identifiant de connexion : on la met en minuscules et on refuse
l'evidemment invalide, sans embarquer un validateur RFC 5322 complet. Partagee par les
schemas admin (UserIn/UserPatch) et « moi » (ProfileIn) pour ne pas diverger.
"""
from __future__ import annotations


def normalize_email(v: str) -> str:
    v = v.strip().lower()
    if "@" not in v or v.startswith("@") or v.endswith("@") or " " in v:
        raise ValueError("adresse e-mail invalide")
    return v
