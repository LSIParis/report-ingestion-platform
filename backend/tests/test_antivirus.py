"""Tests unitaires de l'antivirus (sans clamd).

Le scan réel contre clamd se teste avec la chaîne EICAR :
  echo -n 'X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*'
  → antivirus.scan(...) doit lever VirusFound (nécessite un clamd joignable).
"""
from app.services import antivirus


def test_scan_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(antivirus.settings, "antivirus_enabled", False)
    assert antivirus.scan(b"nimporte quoi") is None


def test_virusfound_carries_signature():
    err = antivirus.VirusFound("Eicar-Test-Signature")
    assert err.signature == "Eicar-Test-Signature"
    assert "Eicar-Test-Signature" in str(err)


def test_unavailable_when_clamd_down(monkeypatch):
    # AV activé mais clamd injoignable → AntivirusUnavailable (fail-safe, pas VirusFound)
    monkeypatch.setattr(antivirus.settings, "antivirus_enabled", True)
    monkeypatch.setattr(antivirus.settings, "clamav_host", "127.0.0.1")
    monkeypatch.setattr(antivirus.settings, "clamav_port", 1)  # port fermé
    import pytest
    with pytest.raises(antivirus.AntivirusUnavailable):
        antivirus.scan(b"data")
