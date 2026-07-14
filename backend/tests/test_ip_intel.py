"""Les faits DNS sur une IP. Résolveur moqué : ces tests ne touchent pas le réseau.

Le test qui compte vraiment est `test_ptr_menteur_nest_pas_verifie` : un PTR se pose
librement sur sa propre IP. Sans l'aller-retour, n'importe qui se déclare Google.
"""
import dns.exception
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
    # 2001:db8::1 → les 32 nibbles, à l'envers. Team Cymru sert l'IPv6 sur origin6.
    name = ("1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0"
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
