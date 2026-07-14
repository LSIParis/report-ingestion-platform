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
