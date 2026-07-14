"""Adaptateur DMARC : décompression, parsing du schéma RUA, gardes de sécurité."""
import gzip
import io
import zipfile

import pytest

from app.parsing.adapters.dmarc_adapter import DmarcXmlAdapter
from app.parsing.base import ParseResult
from app.parsing.compression import DecompressionTooLarge, decompress
from app.parsing.guards import guard_report_domain

XML = """<?xml version="1.0" encoding="UTF-8"?>
<feedback>
  <report_metadata>
    <org_name>google.com</org_name>
    <email>noreply-dmarc-support@google.com</email>
    <report_id>1234567890</report_id>
    <date_range><begin>1751328000</begin><end>1751414399</end></date_range>
  </report_metadata>
  <policy_published>
    <domain>acme.com</domain><adkim>r</adkim><aspf>r</aspf>
    <p>quarantine</p><sp>quarantine</sp><pct>100</pct>
  </policy_published>
  <record>
    <row>
      <source_ip>209.85.220.41</source_ip><count>42</count>
      <policy_evaluated><disposition>none</disposition><dkim>pass</dkim><spf>pass</spf></policy_evaluated>
    </row>
    <identifiers><header_from>acme.com</header_from></identifiers>
    <auth_results>
      <dkim><domain>acme.com</domain><result>pass</result></dkim>
      <spf><domain>acme.com</domain><result>pass</result></spf>
    </auth_results>
  </record>
  <record>
    <row>
      <source_ip>185.53.178.9</source_ip><count>3</count>
      <policy_evaluated><disposition>quarantine</disposition><dkim>fail</dkim><spf>fail</spf></policy_evaluated>
    </row>
    <identifiers><header_from>acme.com</header_from></identifiers>
    <auth_results><spf><domain>evil.example</domain><result>fail</result></spf></auth_results>
  </record>
</feedback>
"""


def _gz(data: bytes) -> bytes:
    return gzip.compress(data)


def _zip(data: bytes, name: str = "report.xml") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, data)
    return buf.getvalue()


# ---------- décompression ----------
def test_decompress_gzip_zip_et_xml_nu_donnent_le_meme_xml():
    raw = XML.encode()
    assert decompress(_gz(raw)) == raw
    assert decompress(_zip(raw)) == raw
    assert decompress(raw) == raw          # certains expéditeurs n'archivent pas


def test_gzip_bomb_est_rejetee():
    # ~100 Mo de zéros compressés en quelques Ko : doit être refusé, pas décompressé.
    bomb = gzip.compress(b"\0" * (100 * 1024 * 1024))
    with pytest.raises(DecompressionTooLarge):
        decompress(bomb)


def test_zip_sans_xml_est_rejete():
    with pytest.raises(ValueError):
        decompress(_zip(b"pas du xml", name="notes.txt"))


# ---------- parsing ----------
def test_parse_extrait_une_ligne_par_record():
    res = DmarcXmlAdapter().parse(_gz(XML.encode()), profile=None)
    assert res.status == "ok"
    assert len(res.rows) == 2

    r0, r1 = res.rows
    assert r0["source_ip"] == "209.85.220.41"
    assert r0["count"] == "42"
    assert r0["aligned"] == "pass"          # dkim=pass OU spf=pass
    assert r0["auth_dkim"] == "acme.com=pass"
    assert r1["disposition"] == "quarantine"
    assert r1["aligned"] == "fail"          # les deux échouent
    assert r1["auth_spf"] == "evil.example=fail"


def test_champs_de_niveau_rapport_sur_chaque_ligne():
    res = DmarcXmlAdapter().parse(XML.encode(), profile=None)
    for row in res.rows:
        assert row["policy_domain"] == "acme.com"
        assert row["org_name"] == "google.com"
        assert row["report_id"] == "1234567890"
        assert row["date_begin"] == "2025-07-01"   # epoch UTC -> date ISO
        assert row["policy_p"] == "quarantine"


def test_xml_malforme_echoue_proprement():
    res = DmarcXmlAdapter().parse(b"<feedback><record>", profile=None)
    assert res.status == "failed"
    assert res.errors[0]["code"] == "DMARC_BAD_XML"


def test_rapport_sans_domaine_de_politique_est_refuse():
    # Sans policy_published/domain, impossible de vérifier le tenant -> refus.
    xml = "<feedback><report_metadata><org_name>x</org_name></report_metadata></feedback>"
    res = DmarcXmlAdapter().parse(xml.encode(), profile=None)
    assert res.status == "failed"
    assert res.errors[0]["code"] == "DMARC_NO_POLICY_DOMAIN"


# ---------- garde d'isolation (le test qui compte) ----------
def test_rapport_dun_autre_domaine_est_rejete_sans_ecrire_de_ligne():
    """Sujet forgé « Report Domain: acme.com » : l'e-mail est résolu vers acme,
    mais le XML concerne globex.com. Aucune ligne ne doit être écrite."""
    parsed = DmarcXmlAdapter().parse(XML.replace("acme.com", "globex.com").encode(),
                                     profile=None)
    assert parsed.rows                                   # le parsing, lui, a réussi

    guarded = guard_report_domain(parsed, tenant_domain="acme.com")
    assert guarded.status == "failed"
    assert guarded.rows == []                            # RIEN n'est persisté
    assert guarded.errors[0]["code"] == "DMARC_DOMAIN_MISMATCH"


def test_domaine_correspondant_passe_la_garde():
    parsed = DmarcXmlAdapter().parse(XML.encode(), profile=None)
    assert guard_report_domain(parsed, tenant_domain="acme.com").rows


def test_sous_domaine_du_tenant_est_accepte():
    # mail.acme.com publie sa propre politique : légitimement rattaché à acme.com.
    parsed = DmarcXmlAdapter().parse(
        XML.replace("<domain>acme.com</domain>", "<domain>mail.acme.com</domain>").encode(),
        profile=None)
    assert guard_report_domain(parsed, tenant_domain="acme.com").rows


def test_domaine_suffixe_trompeur_est_rejete():
    # 'notacme.com' finit par 'acme.com' en simple sous-chaîne : ne doit PAS passer.
    parsed = DmarcXmlAdapter().parse(XML.replace("acme.com", "notacme.com").encode(),
                                     profile=None)
    assert guard_report_domain(parsed, tenant_domain="acme.com").status == "failed"


def test_garde_inoperante_sur_les_formats_non_auto_descriptifs():
    # Un CSV n'a pas de policy_domain : la garde laisse passer sans rien casser.
    csv_like = ParseResult(status="ok", rows=[{"a": 1}], metadata={"row_count": 1})
    assert guard_report_domain(csv_like, tenant_domain="acme.com").status == "ok"
