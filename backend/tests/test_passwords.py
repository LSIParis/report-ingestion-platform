"""Hachage des mots de passe.

Ce test existe parce que la chaîne passlib+bcrypt s'est cassée silencieusement en
production : passlib 1.7.4 est incompatible avec bcrypt >= 4.1 et lève une ValueError
à CHAQUE hachage. Plus aucun compte ne pouvait être créé ni authentifié. Un simple
aller-retour hash/verify l'aurait attrapé.
"""
import pytest

from app.auth.passwords import MAX_BYTES, hash_password, verify_password


def test_aller_retour_hash_verify():
    h = hash_password("un mot de passe correct")
    assert verify_password("un mot de passe correct", h)


def test_mauvais_mot_de_passe_refuse():
    assert not verify_password("mauvais", hash_password("bon"))


def test_empreinte_au_format_bcrypt():
    assert hash_password("x").startswith("$2b$")


def test_deux_hachages_du_meme_secret_different():
    # sel aléatoire : deux empreintes distinctes, toutes deux valides
    a, b = hash_password("meme"), hash_password("meme")
    assert a != b
    assert verify_password("meme", a) and verify_password("meme", b)


def test_accents_et_unicode():
    h = hash_password("mot-de-passé-à-clé-€")
    assert verify_password("mot-de-passé-à-clé-€", h)


def test_mot_de_passe_trop_long_est_refuse():
    # bcrypt tronque au-delà de 72 octets : deux secrets partageant leurs 72 premiers
    # octets seraient interchangeables. On refuse plutôt que de tronquer en silence.
    with pytest.raises(ValueError):
        hash_password("a" * (MAX_BYTES + 1))


def test_empreinte_corrompue_ne_leve_pas():
    assert not verify_password("x", "pas-une-empreinte-bcrypt")
