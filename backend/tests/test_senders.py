"""Le catalogue ne doit JAMAIS mentir.

Un catalogue naïf n'est pas inutile : il est menteur, et rassurant. C'est pire que rien.
Ces tests verrouillent les trois règles qui l'en empêchent.
"""
from app.services import senders
from app.services.ip_intel import IpFacts


def test_ptr_verifie_identifie_l_expediteur():
    facts = IpFacts(ip="1.2.3.4", ptr="o1.ptr1234.sendgrid.net", fcrdns=True)

    m = senders.identify(facts)

    assert m.sender is not None
    assert m.sender.name == "SendGrid"
    assert m.sender.spf_include == "sendgrid.net"
    assert "include:sendgrid.net" in m.sender.remediation


def test_ptr_menteur_n_identifie_personne():
    """LE test. Sans lui, un usurpateur pose un PTR .sendgrid.net sur son IP et notre
    écran le blanchit — au moment précis où il devrait alerter."""
    facts = IpFacts(ip="1.2.3.4", ptr="o1.ptr1234.sendgrid.net", fcrdns=False)

    m = senders.identify(facts)

    assert m.sender is None


def test_asn_seul_ne_nomme_pas_l_expediteur():
    """AS16509 est Amazon — mais l'essentiel de ses IP sont des EC2 quelconques, pas
    Amazon SES. « Hébergé chez » situe ; il ne conclut pas."""
    facts = IpFacts(ip="1.2.3.4", ptr="ec2-1-2-3-4.compute.amazonaws.com", fcrdns=True,
                    asn=16509, as_org="AMAZON-02, US")

    m = senders.identify(facts)

    assert m.sender is None
    assert m.hosted_by == "AMAZON-02, US"


def test_aucune_correspondance_ne_degrade_rien():
    facts = IpFacts(ip="1.2.3.4", ptr="host.inconnu.example", fcrdns=True,
                    asn=64500, as_org="INCONNU-AS, RU")

    m = senders.identify(facts)

    assert m.sender is None
    assert m.hosted_by == "INCONNU-AS, RU"


def test_sans_ptr_ni_asn():
    m = senders.identify(IpFacts(ip="1.2.3.4"))
    assert m.sender is None and m.hosted_by is None


def test_suffixe_le_plus_long_gagne():
    """Un suffixe plus spécifique doit l'emporter — on ne veut pas dépendre de l'ordre
    de lecture des fichiers."""
    facts = IpFacts(ip="1.2.3.4", ptr="mail-1.eu.mail.protection.outlook.com", fcrdns=True)

    m = senders.identify(facts)

    assert m.sender is not None
    assert m.sender.name == "Microsoft 365"


def test_le_catalogue_charge_est_coherent():
    """Chaque fichier doit porter les champs attendus — une faute de frappe dans un JSON
    ne doit pas se découvrir en production."""
    catalogue = senders.load()

    assert catalogue, "catalogue vide : les fichiers backend/senders/*.json sont-ils là ?"
    for s in catalogue:
        assert s.key and s.name and s.remediation
