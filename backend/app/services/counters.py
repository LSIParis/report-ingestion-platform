"""Distinguer « illisible » de « zéro » dans un compteur de sessions TLS.

Partagé entre `tls_posture` (l'écran qui autorise `enforce`) et `ip_intel` (le
panneau IP) : les deux lisent les MÊMES compteurs (`failed_sessions` /
`failure_sessions`) issus des mêmes lignes TLS-RPT persistées, et les deux doivent
refuser le même mensonge -- un compteur absent ou non entier n'est PAS zéro, c'est
une valeur inconnue. Une seule definition évite que les deux lectures divergent un
jour sur ce qui compte comme "illisible".
"""
from __future__ import annotations


def int_or_none(value) -> int | None:
    """`None` en entrée, ou une valeur non castable, renvoie `None` -- jamais 0."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
