# Rapports TLS-RPT — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lire les rapports TLS-RPT (RFC 8460) qui arrivent déjà dans la boîte de collecte, et répondre à la question qu'ils existent pour trancher : « puis-je passer MTA-STS en `enforce` sans perdre de courrier ? »

**Architecture:** La cause racine est un aiguillage par **extension de fichier** (`.gz → dmarc_xml`) qui envoie les rapports TLS à l'adaptateur DMARC. On le remplace par une détection **par contenu**. Le reste de l'architecture (résolution de tenant, garde anti-usurpation, sélection de profil) accepte déjà ce format sans une ligne de code.

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy 2.0 · Alembic · Celery · PostgreSQL 16 (RLS) · React 19 + TanStack Query + Tailwind.

**Spec:** `docs/superpowers/specs/2026-07-14-rapports-tls-rpt-design.md`

## Global Constraints

- **Aucune nouvelle dépendance.** Le JSON est dans la bibliothèque standard.
- **Le contenu décide, jamais le nom du fichier.** Le nom vient de l'expéditeur ; on ne lui fait pas confiance. C'est la règle que le dépôt s'était déjà donnée (`dmarc_adapter`) et que l'aiguillage amont violait.
- **Ne jamais deviner** (CLAUDE.md §6). Un `result-type` inconnu de la RFC est conservé tel quel. L'absence de rapport TLS n'est **pas** une preuve de succès.
- **Parsing tolérant** : une politique corrompue n'invalide pas le rapport entier (`partial`, erreurs collectées par ligne).
- **Routes tenant : aucun `WHERE tenant_id` applicatif.** La RLS fait le travail. Pour les routes admin qui ciblent **un** tenant, ouvrir `tenant_scoped_session(tenant_id=<id>)` **sans bypass** : la RLS scope alors sur ce tenant précis — l'option la plus restrictive.
- **Le test d'isolation bloque le merge** (`tests/test_tenant_isolation.py`).
- **Commandes** : `cd infra && docker compose exec api pytest` · `ruff check app`. Pour itérer vite sans reconstruire l'image (le code est cuit dedans, il n'y a pas de volume) :
  `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" api pytest <chemin>`
- **Clés canoniques des lignes TLS** (après normalisation par `profiles/_default_tlsrpt_json.json`) : `kind` (`summary`|`failure`), `reporter`, `report_id`, `report_date`, `period_end`, `policy_domain`, `policy_type`, `mx_host`, puis `successful_sessions`/`failed_sessions` (lignes `summary`) et `result_type`/`sending_mta_ip`/`receiving_mx_hostname`/`receiving_ip`/`failure_sessions` (lignes `failure`).
- Commentaires et messages de commit en français.

## Structure des fichiers

| Fichier | Responsabilité |
|---|---|
| `backend/app/parsing/compression.py` | **Créer.** Décompression bornée (gzip/zip/nu), extraite du `dmarc_adapter` : le détecteur en a besoin **avant** de savoir quel adaptateur appeler. |
| `backend/app/parsing/adapters/dmarc_adapter.py` | **Modifier.** Importe `compression` au lieu de porter le code. Aucun changement de comportement. |
| `backend/app/parsing/detect.py` | **Créer.** `detect_format(payload, filename)` : le contenu décide. |
| `backend/app/workers/tasks.py` | **Modifier.** `_list_sources` appelle le détecteur. |
| `backend/app/parsing/adapters/tlsrpt_adapter.py` | **Créer.** `@register("tlsrpt_json")`. |
| `backend/profiles/_default_tlsrpt_json.json` | **Créer.** Mapping canonique. Une donnée. |
| `backend/app/services/tls_posture.py` | **Créer.** L'agrégation « puis-je passer en enforce ? », isolée du transport pour être testable seule et réutilisable par une future route tenant. |
| `backend/app/api/admin.py` | **Modifier.** `GET /admin/tenants/{id}/tls-posture`. |
| `backend/migrations/versions/0005_tls_ip_index.py` | **Créer.** Index sur `sending_mta_ip`. |
| `backend/app/api/ip_intel.py` | **Modifier.** Appartenance et activité étendues aux lignes TLS. |
| `frontend/src/api/domains.ts` | **Modifier.** Hook `useTlsPosture`. |
| `frontend/src/components/MtaStsPanel.tsx` | **Modifier.** Le verdict TLS, à côté du sélecteur de mode. |
| `frontend/src/pages/ReportDetail.tsx` | **Modifier.** Rendu des lignes TLS, IP émettrice cliquable. |

Ordre : 1 → 2 → 3 (le pipeline), puis 4 et 5 (indépendants entre eux), puis 6 (l'écran).

---

### Task 1: La décompression, sortie de l'adaptateur DMARC

**Files:**
- Create: `backend/app/parsing/compression.py`
- Modify: `backend/app/parsing/adapters/dmarc_adapter.py`
- Test: `backend/tests/test_compression.py`

**Interfaces:**
- Consumes: rien.
- Produces:
  - `MAX_BYTES: int` (64 Mo)
  - `class DecompressionTooLarge(ValueError)`
  - `decompress(raw: bytes) -> bytes`

Le `dmarc_adapter` porte aujourd'hui `decompress()` et ses bornes anti-bombe. Le détecteur de format (Task 2) en a besoin **avant** de savoir quel adaptateur appeler — il ne peut donc pas le demander au DMARC. On l'extrait.

Un changement de comportement délibéré : l'ancien code n'acceptait dans un ZIP qu'un fichier `.xml`. Un rapport TLS en ZIP serait refusé. On accepte désormais `.xml` **ou** `.json`, avec repli sur la première entrée — mais on garde toutes les bornes.

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `backend/tests/test_compression.py` :

```python
"""Décompression bornée. Le contenu vient d'Internet et n'est pas authentifié : une
archive de 1 Ko peut se décompresser en 10 Go et faire tomber le worker. Les bornes ne
sont pas une optimisation, ce sont des gardes.
"""
import gzip
import io
import zipfile

import pytest

from app.parsing.compression import DecompressionTooLarge, decompress


def test_contenu_nu_est_rendu_tel_quel():
    assert decompress(b"<feedback/>") == b"<feedback/>"


def test_gzip():
    raw = gzip.compress(b'{"report-id": "x"}')
    assert decompress(raw) == b'{"report-id": "x"}'


def test_zip_xml():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("rapport.xml", "<feedback/>")
    assert decompress(buf.getvalue()) == b"<feedback/>"


def test_zip_json_est_accepte():
    # L'ancien code n'acceptait QUE des .xml dans un zip : un rapport TLS zippé était
    # refusé avant même d'être lu.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("rapport.json", '{"report-id": "x"}')
    assert decompress(buf.getvalue()) == b'{"report-id": "x"}'


def test_zip_vide_est_une_erreur():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w"):
        pass
    with pytest.raises(ValueError):
        decompress(buf.getvalue())


def test_bombe_gzip_est_bornee():
    # 200 Mo de zéros compressent en quelques Ko. Sans borne, on les décompresse tous.
    bombe = gzip.compress(b"\0" * (200 * 1024 * 1024))
    with pytest.raises(DecompressionTooLarge):
        decompress(bombe)
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" api pytest tests/test_compression.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.parsing.compression'`

- [ ] **Step 3: Écrire le module**

Créer `backend/app/parsing/compression.py` :

```python
"""Décompression bornée des pièces jointes.

Le contenu vient d'Internet et n'est pas authentifié : une archive de quelques kilo-octets
peut se décompresser en plusieurs giga-octets et faire tomber le worker. Les bornes ne
sont pas une optimisation, ce sont des gardes.

Ce code vivait dans `dmarc_adapter`. Le détecteur de format en a besoin **avant** de
savoir quel adaptateur appeler — il ne peut donc pas le lui demander. D'où l'extraction.
"""
from __future__ import annotations

import gzip
import io
import zipfile

# Un rapport réel pèse quelques dizaines de Ko à quelques Mo. 64 Mo décompressés est déjà
# très large : au-delà, c'est une bombe, pas un rapport.
MAX_BYTES = 64 * 1024 * 1024
_CHUNK = 1 << 20


class DecompressionTooLarge(ValueError):
    """L'archive dépasse la taille décompressée autorisée (bombe probable)."""


def decompress(raw: bytes) -> bytes:
    """gzip, zip ou contenu nu → octets. Détection par nombre magique, pas par extension
    (le nom de fichier vient de l'expéditeur, on ne lui fait pas confiance)."""
    if raw[:2] == b"\x1f\x8b":
        return _bounded_read(gzip.GzipFile(fileobj=io.BytesIO(raw)))

    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            entries = [n for n in z.namelist() if not n.endswith("/")]
            if not entries:
                raise ValueError("archive zip vide")
            # Un rapport (XML pour DMARC, JSON pour TLS-RPT) d'abord ; à défaut, la
            # première entrée — on ne suppose pas l'extension, on la préfère seulement.
            name = next(
                (n for n in entries if n.lower().endswith((".xml", ".json"))),
                entries[0],
            )
            # On se fie à la taille ANNONCÉE pour rejeter tôt, puis on borne quand même
            # la lecture : un en-tête zip peut mentir.
            if z.getinfo(name).file_size > MAX_BYTES:
                raise DecompressionTooLarge(f"{name} annonce une taille excessive")
            return _bounded_read(z.open(name))

    return raw


def _bounded_read(stream) -> bytes:
    out = io.BytesIO()
    with stream as f:
        while chunk := f.read(_CHUNK):
            out.write(chunk)
            if out.tell() > MAX_BYTES:
                raise DecompressionTooLarge(f"contenu décompressé > {MAX_BYTES} octets")
    return out.getvalue()
```

- [ ] **Step 4: Faire pointer le `dmarc_adapter` dessus**

Dans `backend/app/parsing/adapters/dmarc_adapter.py` :

Supprimer les imports `gzip`, `io`, `zipfile`, les constantes `MAX_XML_BYTES` / `_CHUNK`,
la classe `DecompressionTooLarge`, et les fonctions `decompress` / `_bounded_read`.

Ajouter l'import :

```python
from app.parsing.compression import DecompressionTooLarge, decompress
```

Le corps de `parse()` référence déjà `decompress(...)`, `DecompressionTooLarge` et
`zipfile.BadZipFile` dans son `except`. Remplacer cette clause :

```python
        except (DecompressionTooLarge, ValueError, OSError, zipfile.BadZipFile) as exc:
```

par (une archive corrompue lève `zipfile.BadZipFile`, qui **hérite de `ValueError`** —
déjà couvert) :

```python
        except (DecompressionTooLarge, ValueError, OSError) as exc:
```

- [ ] **Step 5: Lancer les tests — y compris ceux du DMARC, qui ne doivent pas bouger**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" api pytest tests/test_compression.py tests/ -q -k "compression or dmarc"`
Expected: PASS — les tests DMARC existants passent inchangés (c'est un déplacement, pas une réécriture)

- [ ] **Step 6: Lint + commit**

```bash
cd infra && docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" api ruff check app
```

```bash
git add backend/app/parsing/compression.py backend/app/parsing/adapters/dmarc_adapter.py backend/tests/test_compression.py
git commit -m "refactor(parsing): la decompression sort de l adaptateur DMARC

Le detecteur de format en a besoin AVANT de savoir quel adaptateur appeler : il ne
peut pas le demander au DMARC. Les bornes anti-bombe sont conservees a l identique.

Un changement deliberé : un ZIP ne devait contenir qu un .xml. Un rapport TLS zippe
etait donc refuse avant meme d etre lu."
```

---

### Task 2: Le contenu décide, pas le nom du fichier

**Files:**
- Create: `backend/app/parsing/detect.py`
- Modify: `backend/app/workers/tasks.py` (constante `EXT_TO_FORMAT` lignes 30-35, et `_list_sources` lignes 131-141)
- Test: `backend/tests/test_detect.py`

**Interfaces:**
- Consumes: `decompress(raw) -> bytes` et `DecompressionTooLarge` (Task 1).
- Produces: `detect_format(payload: bytes, filename: str | None) -> str | None` — renvoie `"dmarc_xml"`, `"tlsrpt_json"`, `"csv"`, `"xlsx"`, `"pdf"`, ou `None` (pièce jointe ignorée).

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `backend/tests/test_detect.py` :

```python
"""Le contenu décide, jamais le nom du fichier.

Le nom vient de l'expéditeur. S'y fier était la cause racine du bug : un rapport TLS
s'appelle `…json.gz`, l'extension `.gz` était câblée sur « dmarc_xml », et le rapport
partait à l'adaptateur DMARC pour y mourir en DMARC_BAD_XML.
"""
import gzip
import io
import zipfile

from app.parsing.detect import detect_format

XML = b"<?xml version='1.0'?><feedback><report_metadata/></feedback>"
JSON = b'{"organization-name": "Google Inc.", "policies": []}'


def test_xml_nu():
    assert detect_format(XML, "rapport.xml") == "dmarc_xml"


def test_json_nu():
    assert detect_format(JSON, "rapport.json") == "tlsrpt_json"


def test_gz_contenant_du_xml():
    assert detect_format(gzip.compress(XML), "acme!exemple.fr!1!2.xml.gz") == "dmarc_xml"


def test_gz_contenant_du_json_est_un_rapport_TLS():
    """LE cas qui cassait : extension .gz, contenu JSON."""
    nom = "google.com!exemple.fr!1752!1752.json.gz"
    assert detect_format(gzip.compress(JSON), nom) == "tlsrpt_json"


def test_zip_contenant_du_xml():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("r.xml", XML)
    assert detect_format(buf.getvalue(), "r.zip") == "dmarc_xml"


def test_extension_mensongere_le_contenu_gagne():
    """Un fichier nommé .xml qui contient du JSON est un rapport TLS. Le nom ment ;
    le contenu, non."""
    assert detect_format(JSON, "rapport.xml") == "tlsrpt_json"


def test_sans_extension_du_tout():
    assert detect_format(JSON, "piece-jointe") == "tlsrpt_json"


def test_espaces_et_BOM_avant_le_premier_octet_significatif():
    assert detect_format(b"\xef\xbb\xbf\n  " + JSON, "r.json") == "tlsrpt_json"
    assert detect_format(b"\n\t " + XML, "r.xml") == "dmarc_xml"


def test_formats_tabulaires_restent_aiguilles_par_extension():
    # Un CSV n'a pas de signature : son extension est la seule information disponible.
    assert detect_format(b"col1;col2\n1;2", "rapport.csv") == "csv"
    assert detect_format(b"%PDF-1.4", "rapport.pdf") == "pdf"


def test_contenu_inexploitable_est_ignore():
    assert detect_format(b"bonjour", "notes.txt") is None
    assert detect_format(b"", "vide.gz") is None


def test_archive_corrompue_est_ignoree_sans_lever():
    # Le worker ne doit pas tomber sur une pièce jointe pourrie.
    assert detect_format(b"\x1f\x8bcasse", "r.gz") is None
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" api pytest tests/test_detect.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.parsing.detect'`

- [ ] **Step 3: Écrire le détecteur**

Créer `backend/app/parsing/detect.py` :

```python
"""Quel format est cette pièce jointe ? Le CONTENU décide, jamais le nom.

Le nom du fichier vient de l'expéditeur — un tiers non authentifié. Le `dmarc_adapter`
l'écrivait déjà noir sur blanc pour la décompression… mais l'aiguillage en AMONT faisait
exactement l'inverse, et c'était la cause racine :

    ".gz": "dmarc_xml"

Un rapport TLS-RPT s'appelle `google.com!exemple.fr!1752!1752.json.gz`. Extension `.gz`,
donc « DMARC », donc décompression, donc du JSON offert à un parseur XML, donc
DMARC_BAD_XML. Les rapports TLS n'étaient pas ignorés : ils étaient rangés en `failed`.

Les formats tabulaires (CSV, XLSX, PDF) n'ont pas de signature exploitable ici et ne sont
pas ambigus : leur extension reste l'aiguillage. L'ambiguïté ne concernait que les
rapports normalisés — c'est là, et seulement là, qu'on regarde le contenu.
"""
from __future__ import annotations

from app.parsing.compression import decompress

# Formats non ambigus : l'extension suffit, et c'est la seule information disponible
# (un CSV n'a pas de nombre magique).
_BY_EXTENSION = {
    ".csv": "csv",
    ".xlsx": "xlsx",
    ".xls": "xlsx",
    ".pdf": "pdf",
}

# Extensions qui PEUVENT porter un rapport normalisé — DMARC ou TLS-RPT. Le contenu
# tranchera. L'absence d'extension en fait partie : un expéditeur peut très bien ne pas
# en mettre.
_MAYBE_REPORT = {".gz", ".zip", ".xml", ".json", ""}


def _ext(filename: str | None) -> str:
    if not filename:
        return ""
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot >= 0 else ""


def detect_format(payload: bytes, filename: str | None) -> str | None:
    """Le format à passer au registre d'adaptateurs, ou None si rien d'exploitable."""
    ext = _ext(filename)

    known = _BY_EXTENSION.get(ext)
    if known:
        return known

    if ext not in _MAYBE_REPORT:
        return None

    try:
        content = decompress(payload)
    except Exception:  # noqa: BLE001 — archive corrompue, tronquée, hostile : on ignore
        return None

    # Premier octet significatif : BOM et blancs ne disent rien du format.
    body = content.lstrip(b"\xef\xbb\xbf").lstrip()
    if not body:
        return None
    if body[:1] == b"{":
        return "tlsrpt_json"
    if body[:1] == b"<":
        return "dmarc_xml"
    return None
```

- [ ] **Step 4: Brancher l'orchestrateur**

Dans `backend/app/workers/tasks.py` :

Supprimer la constante `EXT_TO_FORMAT` (lignes 30-35) et remplacer l'import :

```python
from app.parsing.detect import detect_format
```

Dans `_list_sources`, remplacer ce bloc :

```python
        ext = _ext(filename)
        fmt = EXT_TO_FORMAT.get(ext)
        if not fmt:
            continue  # format non géré → ignoré (traçable via metadata si besoin)

        payload = part.get_payload(decode=True) or b""
```

par (on lit la charge utile **avant** de décider — c'est elle qui décide) :

```python
        payload = part.get_payload(decode=True) or b""

        # Le CONTENU décide, pas le nom : `…json.gz` est un rapport TLS, pas du DMARC.
        fmt = detect_format(payload, filename)
        if not fmt:
            continue  # rien d'exploitable → ignoré
```

Supprimer la fonction `_ext` en fin de fichier (lignes 234-236) : elle n'a plus d'appelant.

- [ ] **Step 5: Lancer la suite complète**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" api pytest -q`
Expected: PASS — la détection remplace l'aiguillage sans rien casser

- [ ] **Step 6: Lint + commit**

```bash
git add backend/app/parsing/detect.py backend/app/workers/tasks.py backend/tests/test_detect.py
git commit -m "fix(parsing): le contenu decide du format, plus le nom du fichier

La cause racine tenait en une ligne : \".gz\": \"dmarc_xml\". Un rapport TLS s appelle
google.com!exemple.fr!1752!1752.json.gz — extension .gz, donc DMARC, donc du JSON
offert a un parseur XML, donc DMARC_BAD_XML. Les rapports TLS n etaient pas ignores :
ils etaient ranges en failed.

Le depot savait pourtant que se fier a l extension est une faute — le dmarc_adapter
l ecrit noir sur blanc. L aiguillage en amont faisait l inverse.

Effet de bord souhaitable : un rapport DMARC mal nomme est desormais lu lui aussi."
```

---

### Task 3: L'adaptateur TLS-RPT et son profil

**Files:**
- Create: `backend/app/parsing/adapters/tlsrpt_adapter.py`
- Modify: `backend/app/parsing/adapters/__init__.py` (enregistrer l'adaptateur)
- Create: `backend/profiles/_default_tlsrpt_json.json`
- Test: `backend/tests/test_tlsrpt_adapter.py`

**Interfaces:**
- Consumes: `decompress` (Task 1), `ParseResult` / `ReportAdapter` (`app.parsing.base`), `register` (`app.parsing.registry`), `guard_report_domain` (`app.parsing.guards`, inchangé).
- Produces: format `"tlsrpt_json"` dans le registre. Lignes canoniques listées dans les Global Constraints.

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `backend/tests/test_tlsrpt_adapter.py` :

```python
"""Rapports TLS-RPT (RFC 8460).

Deux tests portent le poids :
 - `test_pas_de_double_comptage_des_echecs` : le résumé et le détail comptent les MÊMES
   sessions. S'ils partageaient un nom de champ, un SUM() sur la table les additionnerait
   et la statistique la plus regardée de l'écran serait fausse, en silence.
 - `test_rapport_pour_un_autre_domaine_est_rejete` : le garde anti-usurpation existant
   doit couvrir ce format sans modification. Le test le PROUVE, il ne le suppose pas.
"""
import gzip
import json

from app.normalization.profiles import load_profile
from app.parsing.guards import guard_report_domain
from app.parsing.registry import get_adapter

PROFILE = load_profile("_default_tlsrpt_json")


def _rapport(policies=None, domain="exemple.fr") -> bytes:
    return json.dumps({
        "organization-name": "Google Inc.",
        "date-range": {"start-datetime": "2026-07-13T00:00:00Z",
                       "end-datetime": "2026-07-13T23:59:59Z"},
        "contact-info": "smtp-tls-reporting@google.com",
        "report-id": "2026-07-13T00:00:00Z_exemple.fr",
        "policies": policies if policies is not None else [{
            "policy": {"policy-type": "sts",
                       "policy-string": ["version: STSv1", "mode: testing"],
                       "policy-domain": domain,
                       "mx-host": ["*.mail.protection.outlook.com"]},
            "summary": {"total-successful-session-count": 100,
                        "total-failure-session-count": 3},
            "failure-details": [{
                "result-type": "certificate-host-mismatch",
                "sending-mta-ip": "203.0.113.5",
                "receiving-mx-hostname": "mx-backup.exemple.fr",
                "receiving-ip": "198.51.100.7",
                "failed-session-count": 3,
            }],
        }],
    }).encode()


def _parse(raw: bytes):
    return get_adapter("tlsrpt_json").parse(raw, PROFILE)


def test_rapport_nominal():
    r = _parse(_rapport())

    assert r.status == "ok"
    assert r.metadata["policy_domain"] == "exemple.fr"

    summary = [x for x in r.rows if x["kind"] == "summary"]
    failure = [x for x in r.rows if x["kind"] == "failure"]
    assert len(summary) == 1 and len(failure) == 1

    assert summary[0]["successful_sessions"] == 100
    assert summary[0]["failed_sessions"] == 3
    assert summary[0]["reporter"] == "Google Inc."
    assert summary[0]["report_date"] == "2026-07-13"
    assert summary[0]["mx_host"] == "*.mail.protection.outlook.com"

    assert failure[0]["result_type"] == "certificate-host-mismatch"
    assert failure[0]["sending_mta_ip"] == "203.0.113.5"
    assert failure[0]["receiving_mx_hostname"] == "mx-backup.exemple.fr"
    assert failure[0]["failure_sessions"] == 3


def test_pas_de_double_comptage_des_echecs():
    """Le résumé dit « 3 échecs ». Le détail dit « 3 sessions échouées ». Ce sont LES
    MÊMES. Les champs portent donc des noms distincts, pour qu'un SUM() naïf ne puisse
    pas les additionner."""
    rows = _parse(_rapport()).rows

    summary = next(x for x in rows if x["kind"] == "summary")
    failure = next(x for x in rows if x["kind"] == "failure")

    assert "failed_sessions" in summary and "failure_sessions" not in summary
    assert "failure_sessions" in failure and "failed_sessions" not in failure


def test_rapport_sans_aucun_echec():
    r = _parse(_rapport(policies=[{
        "policy": {"policy-type": "sts", "policy-domain": "exemple.fr",
                   "mx-host": ["mx.exemple.fr"]},
        "summary": {"total-successful-session-count": 5000,
                    "total-failure-session-count": 0},
    }]))

    assert r.status == "ok"
    assert len(r.rows) == 1
    assert r.rows[0]["kind"] == "summary"
    assert r.rows[0]["successful_sessions"] == 5000
    assert r.rows[0]["failed_sessions"] == 0


def test_rapport_compresse():
    r = _parse(gzip.compress(_rapport()))
    assert r.status == "ok"


def test_json_malforme():
    r = _parse(b"{ ceci n est pas du json")
    assert r.status == "failed"
    assert r.errors[0]["code"] == "TLSRPT_BAD_JSON"


def test_sans_policy_domain_on_refuse_plutot_que_deviner():
    r = _parse(json.dumps({
        "organization-name": "X", "report-id": "y",
        "date-range": {"start-datetime": "2026-07-13T00:00:00Z",
                       "end-datetime": "2026-07-13T23:59:59Z"},
        "policies": [{"policy": {"policy-type": "sts"},
                      "summary": {"total-successful-session-count": 1,
                                  "total-failure-session-count": 0}}],
    }).encode())
    assert r.status == "failed"
    assert r.errors[0]["code"] == "TLSRPT_NO_POLICY_DOMAIN"


def test_result_type_inconnu_est_conserve_tel_quel():
    """La RFC évoluera. On ne traduit pas, on ne devine pas : on garde ce qui est écrit."""
    r = _parse(_rapport(policies=[{
        "policy": {"policy-type": "sts", "policy-domain": "exemple.fr", "mx-host": []},
        "summary": {"total-successful-session-count": 0,
                    "total-failure-session-count": 1},
        "failure-details": [{"result-type": "quelque-chose-de-nouveau",
                             "failed-session-count": 1}],
    }]))

    failure = next(x for x in r.rows if x["kind"] == "failure")
    assert failure["result_type"] == "quelque-chose-de-nouveau"


def test_politique_corrompue_parmi_d_autres_ne_perd_pas_les_bonnes():
    r = _parse(_rapport(policies=[
        {"policy": {"policy-type": "sts", "policy-domain": "exemple.fr", "mx-host": []},
         "summary": {"total-successful-session-count": 10,
                     "total-failure-session-count": 0}},
        {"policy": {"policy-type": "sts", "policy-domain": "exemple.fr"},
         "summary": "ceci devrait être un objet"},
    ]))

    assert r.status == "partial"
    assert any(x["kind"] == "summary" and x["successful_sessions"] == 10 for x in r.rows)
    assert any(e["code"] == "TLSRPT_BAD_POLICY" for e in r.errors)


def test_rapport_pour_un_autre_domaine_est_rejete():
    """Le sujet du mail est contrôlé par l'expéditeur : n'importe qui peut forger
    « Report Domain: client-a.com ». Le garde existant recoupe le CONTENU — et il doit
    couvrir TLS-RPT sans une ligne de code en plus."""
    parsed = _parse(_rapport(domain="victime.com"))

    garde = guard_report_domain(parsed, "exemple.fr")

    assert garde.status == "failed"
    assert garde.rows == []
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" api pytest tests/test_tlsrpt_adapter.py -q`
Expected: FAIL — `LookupError: Aucun adaptateur pour le format 'tlsrpt_json'` (ou `FileNotFoundError` sur le profil)

- [ ] **Step 3: Écrire le profil**

Créer `backend/profiles/_default_tlsrpt_json.json` :

```json
{
  "profile_id": "_default_tlsrpt_json",
  "tenant_id": "*",
  "format": "tlsrpt_json",
  "detection": {},
  "field_mapping": {
    "kind":                  { "target": "kind",                  "type": "string", "required": true },
    "date_begin":            { "target": "report_date",           "type": "date", "format": "%Y-%m-%d", "required": true },
    "date_end":              { "target": "period_end",            "type": "date", "format": "%Y-%m-%d" },
    "reporter":              { "target": "reporter",              "type": "string", "required": true },
    "report_id":             { "target": "report_id",             "type": "string", "required": true },
    "policy_domain":         { "target": "policy_domain",         "type": "string", "required": true },
    "policy_type":           { "target": "policy_type",           "type": "string" },
    "mx_host":               { "target": "mx_host",               "type": "string" },
    "successful_sessions":   { "target": "successful_sessions",   "type": "int" },
    "failed_sessions":       { "target": "failed_sessions",       "type": "int" },
    "result_type":           { "target": "result_type",           "type": "string" },
    "sending_mta_ip":        { "target": "sending_mta_ip",        "type": "string" },
    "receiving_mx_hostname": { "target": "receiving_mx_hostname", "type": "string" },
    "receiving_ip":          { "target": "receiving_ip",          "type": "string" },
    "failure_sessions":      { "target": "failure_sessions",      "type": "int" }
  },
  "validation": {}
}
```

**Attention** : seuls les champs présents dans les DEUX natures de lignes sont `required`.
Marquer `successful_sessions` comme requis rejetterait toutes les lignes `failure`, et
inversement — la normalisation viderait la moitié de chaque rapport.

- [ ] **Step 4: Écrire l'adaptateur**

Créer `backend/app/parsing/adapters/tlsrpt_adapter.py` :

```python
"""Rapports TLS-RPT (RFC 8460) — l'instrument sans lequel MTA-STS est aveugle.

Ils disent, jour après jour, si le courrier entrant du domaine est réellement chiffré, et
qui échoue quand il ne l'est pas. C'est ce qui rend le passage de MTA-STS en `enforce`
sûr : sans eux, on durcit à l'aveugle, et un expéditeur qui n'arrive pas à valider le
certificat cesse simplement de livrer — sans alerte, sans trace de notre côté.

Le format est du JSON (RFC 8460 §4), livré compressé ou nu. Une ligne canonique = une
OBSERVATION, comme un `<record>` DMARC :

  - `kind: "summary"` — une par politique : le bilan chiffré des sessions.
  - `kind: "failure"` — une par échec détaillé : le type, le MTA émetteur, le MX visé.

**Les compteurs portent des noms différents à dessein** : `failed_sessions` dans le
résumé, `failure_sessions` dans le détail. Ce sont les MÊMES sessions, comptées deux fois
sous deux angles. S'ils partageaient un nom, un `SUM()` sur la table les additionnerait —
et la statistique la plus regardée de l'écran (« combien d'échecs ? ») serait fausse, sans
que rien ne le signale. Le schéma rend la faute impossible plutôt que de compter sur la
vigilance de celui qui écrira la requête dans six mois.
"""
from __future__ import annotations

import json

from app.parsing.base import ParseResult, ReportAdapter
from app.parsing.compression import DecompressionTooLarge, decompress
from app.parsing.registry import register


def _date(value: str | None) -> str | None:
    """« 2026-07-13T00:00:00Z » → « 2026-07-13 ». On ne garde que le jour : la fenêtre
    d'un rapport TLS-RPT est journalière."""
    if not value or len(value) < 10:
        return None
    return value[:10]


@register("tlsrpt_json")
class TlsRptAdapter(ReportAdapter):
    format = "tlsrpt_json"

    def parse(self, raw: bytes, profile) -> ParseResult:
        try:
            content = decompress(raw)
        except (DecompressionTooLarge, ValueError, OSError) as exc:
            return ParseResult(status="failed",
                               errors=[{"code": "TLSRPT_DECOMPRESS",
                                        "message": str(exc), "severity": "fatal"}])

        try:
            doc = json.loads(content)
            if not isinstance(doc, dict):
                raise ValueError("le document racine n'est pas un objet JSON")
        except (ValueError, UnicodeDecodeError) as exc:
            return ParseResult(status="failed",
                               errors=[{"code": "TLSRPT_BAD_JSON",
                                        "message": str(exc), "severity": "fatal"}])

        date_range = doc.get("date-range") or {}
        header = {
            "reporter": doc.get("organization-name"),
            "report_id": doc.get("report-id"),
            "date_begin": _date(date_range.get("start-datetime")),
            "date_end": _date(date_range.get("end-datetime")),
        }

        policies = doc.get("policies") or []
        rows: list[dict] = []
        errors: list[dict] = []
        policy_domain: str | None = None

        for idx, entry in enumerate(policies):
            try:
                rows += self._policy_rows(entry, header)
                policy_domain = policy_domain or _policy_domain(entry)
            except Exception as exc:  # noqa: BLE001 — une politique pourrie n'invalide
                errors.append({"code": "TLSRPT_BAD_POLICY", "row_index": idx,
                               "message": str(exc), "severity": "error"})

        if not policy_domain:
            # Sans domaine de politique, impossible de vérifier à quel tenant ce rapport
            # appartient → on refuse plutôt que de deviner (invariant §6). C'est ce champ
            # que `guard_report_domain` recoupe avec le tenant résolu depuis le sujet.
            return ParseResult(
                status="failed",
                errors=[*errors, {"code": "TLSRPT_NO_POLICY_DOMAIN", "severity": "fatal",
                                  "message": "aucun policy-domain exploitable"}],
                metadata=header)

        meta = {**header, "policy_domain": policy_domain, "row_count": len(rows)}

        if not rows:
            errors.append({"code": "TLSRPT_NO_POLICY", "severity": "error",
                           "message": "rapport sans politique exploitable"})
            return ParseResult(status="failed", errors=errors, metadata=meta)

        return ParseResult(status="partial" if errors else "ok",
                           rows=rows, errors=errors, metadata=meta)

    @staticmethod
    def _policy_rows(entry: dict, header: dict) -> list[dict]:
        policy = entry["policy"]
        domain = policy.get("policy-domain")

        # `mx-host` est une LISTE dans la RFC (une politique peut couvrir plusieurs MX).
        mx = policy.get("mx-host") or []
        if isinstance(mx, str):
            mx = [mx]

        common = {
            **header,
            "policy_domain": domain,
            "policy_type": policy.get("policy-type"),
            "mx_host": ", ".join(mx),
        }

        summary = entry.get("summary") or {}
        rows = [{
            **common,
            "kind": "summary",
            "successful_sessions": summary["total-successful-session-count"],
            "failed_sessions": summary["total-failure-session-count"],
        }]

        for failure in entry.get("failure-details") or []:
            rows.append({
                **common,
                "kind": "failure",
                "result_type": failure.get("result-type"),
                "sending_mta_ip": failure.get("sending-mta-ip"),
                "receiving_mx_hostname": failure.get("receiving-mx-hostname"),
                "receiving_ip": failure.get("receiving-ip"),
                "failure_sessions": failure.get("failed-session-count"),
            })

        return rows


def _policy_domain(entry: dict) -> str | None:
    policy = entry.get("policy")
    return policy.get("policy-domain") if isinstance(policy, dict) else None
```

- [ ] **Step 5: Enregistrer l'adaptateur**

Le registre se peuple à l'import. Remplacer entièrement
`backend/app/parsing/adapters/__init__.py` par :

```python
# Importer les adaptateurs enregistre leurs classes dans le registre.
from app.parsing.adapters import (  # noqa: F401
    body_adapter,
    csv_adapter,
    dmarc_adapter,
    pdf_adapter,
    tlsrpt_adapter,
    xlsx_adapter,
)
```

- [ ] **Step 6: Lancer les tests**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" api pytest tests/test_tlsrpt_adapter.py -q`
Expected: PASS (9 tests)

- [ ] **Step 7: Vérifier que le profil est bien embarqué dans l'image**

`backend/Dockerfile` copie `profiles/` explicitement (ligne 25) — le nouveau fichier y est
donc inclus automatiquement. Vérifier :

```bash
cd infra && docker compose up --build -d api
docker compose exec api python -c "from app.normalization.profiles import load_profile; print(load_profile('_default_tlsrpt_json').profile_id)"
```
Expected: `_default_tlsrpt_json`

- [ ] **Step 8: Lint + commit**

```bash
git add backend/app/parsing/adapters/tlsrpt_adapter.py backend/app/parsing/adapters/__init__.py backend/profiles/_default_tlsrpt_json.json backend/tests/test_tlsrpt_adapter.py
git commit -m "feat(tlsrpt): adaptateur des rapports TLS-RPT (RFC 8460)

Une ligne = une observation, comme un record DMARC : un resume chiffre par politique,
un detail par echec.

Les compteurs portent des noms differents a dessein — failed_sessions dans le resume,
failure_sessions dans le detail. Ce sont les MEMES sessions vues sous deux angles :
avec un nom commun, un SUM() les additionnerait et la statistique la plus regardee de
l ecran serait fausse, en silence.

Le garde anti-usurpation existant couvre ce format sans une ligne de code : il recoupe
metadata[policy_domain], que l adaptateur remplit. Un test le PROUVE."
```

---

### Task 4: L'IP émettrice d'un échec TLS devient cliquable

**Files:**
- Create: `backend/migrations/versions/0005_tls_ip_index.py`
- Modify: `backend/app/api/ip_intel.py` (`_rows_de_cette_ip` et `_activite`)
- Modify: `backend/tests/test_tenant_isolation.py` (test bloquant)
- Test: `backend/tests/test_ip_intel_api.py` (compléter)

**Interfaces:**
- Consumes: la route `/ip-intel/{ip}` existante.
- Produces: `activity` gagne `tls_sessions: int` et `tls_failures: dict[str, int]`.

Le contrôle d'appartenance cherche `data->>'source_ip'`. Une ligne TLS porte
`sending_mta_ip`. **Sans extension, cliquer une IP TLS renverrait 404 sur une IP que le
tenant voit pourtant dans ses propres rapports.**

On ne renomme pas `sending_mta_ip` en `source_ip` : les deux ne disent pas la même chose
(un expéditeur évalué par DMARC / un MTA qui a tenté une session TLS), et le front
distingue une ligne DMARC par la présence de `source_ip`.

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à `backend/tests/test_ip_intel_api.py` — d'abord une fixture qui sème une ligne
TLS, puis les tests :

```python
@pytest.fixture
def ligne_tls(tenant_avec_ligne_dmarc):
    """Une ligne d'échec TLS portant une IP qu'aucune ligne DMARC ne connaît."""
    tid, _, rep_id = tenant_avec_ligne_dmarc
    with get_session() as db:
        db.add(ReportRow(tenant_id=tid, report_id=rep_id, data={
            "kind": "failure", "result_type": "certificate-host-mismatch",
            "sending_mta_ip": "203.0.113.44",
            "receiving_mx_hostname": "mx-backup.ip-test.example",
            "failure_sessions": 7, "policy_domain": "ip-test.example",
            "reporter": "Google Inc.", "report_date": "2026-07-13",
        }))
        db.commit()
    yield "203.0.113.44"
    with get_session() as db:
        db.query(ReportRow).filter(
            ReportRow.data["sending_mta_ip"].astext == "203.0.113.44").delete(
            synchronize_session=False)
        db.query(IpIntel).filter_by(ip="203.0.113.44").delete()
        db.commit()


def test_ip_vue_uniquement_en_TLS_est_consultable(client_du_tenant, ligne_tls):
    """Sans l'extension du contrôle d'appartenance, cette IP donnerait 404 — alors que le
    tenant la voit dans ses propres rapports."""
    client, _ = client_du_tenant

    r = client.get(f"/ip-intel/{ligne_tls}")

    assert r.status_code == 200


def test_activite_TLS_est_comptee(client_du_tenant, ligne_tls):
    """Sinon le panneau afficherait « 0 message » sur une IP qui a bel et bien échoué."""
    client, _ = client_du_tenant

    a = client.get(f"/ip-intel/{ligne_tls}").json()["activity"]

    assert a["messages"] == 0                      # aucune ligne DMARC : c'est vrai
    assert a["tls_sessions"] == 7
    assert a["tls_failures"] == {"certificate-host-mismatch": 7}
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" api pytest tests/test_ip_intel_api.py -q`
Expected: FAIL — 404 sur l'IP TLS, et `KeyError: 'tls_sessions'`

- [ ] **Step 3: Écrire la migration**

Créer `backend/migrations/versions/0005_tls_ip_index.py` :

```python
"""L'IP émettrice d'un échec TLS doit être aussi consultable qu'une IP source DMARC.

Le contrôle d'appartenance de /ip-intel cherche désormais dans les DEUX champs. Sans cet
index, chaque ouverture du panneau sur une IP TLS déclencherait un seq scan sur
report_row.

Revision ID: 0005_tls_ip_index
"""
from alembic import op

revision = "0005_tls_ip_index"
down_revision = "0004_ip_intel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX ix_report_row_sending_mta_ip
          ON report_row (tenant_id, (data->>'sending_mta_ip'));
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_report_row_sending_mta_ip;")
```

- [ ] **Step 4: Appliquer la migration**

Run: `cd infra && MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" migrate alembic upgrade head`
Expected: `Running upgrade 0004_ip_intel -> 0005_tls_ip_index`

- [ ] **Step 5: Étendre le contrôle d'appartenance**

Dans `backend/app/api/ip_intel.py`, ajouter l'import :

```python
from sqlalchemy import or_
```

Remplacer `_rows_de_cette_ip` :

```python
def _rows_de_cette_ip(db, ip: str) -> list[ReportRow]:
    """Les lignes de rapport où cette IP apparaît — SOUS RLS.

    Deux champs, deux sens : `source_ip` est un expéditeur évalué par DMARC,
    `sending_mta_ip` un MTA qui a tenté une session TLS. On ne les confond pas — mais une
    IP qui échoue en TLS mérite la même enquête, et le tenant la voit dans ses rapports :
    la lui refuser par 404 serait absurde.

    Aucun `WHERE tenant_id` applicatif : la session est déjà scopée (CLAUDE.md). Une IP
    vue par un autre tenant ne renverra rien ici, et c'est exactement le but.
    """
    return (db.query(ReportRow)
              .filter(or_(ReportRow.data["source_ip"].astext == ip,
                          ReportRow.data["sending_mta_ip"].astext == ip))
              .all())
```

- [ ] **Step 6: Compter l'activité TLS**

Dans `backend/app/api/ip_intel.py`, fonction `_activite` : ajouter deux accumulateurs et
les exposer.

Après la ligne `dates: list[str] = []`, ajouter :

```python
    tls_sessions = 0
    tls_failures: Counter[str] = Counter()
```

Dans la boucle `for r in rows:`, juste après `d = r.data`, ajouter (une ligne TLS n'a pas
de `message_count` : la traiter comme une ligne DMARC produirait des zéros trompeurs) :

```python
        if d.get("kind") == "failure":
            n_tls = d.get("failure_sessions") or 0
            try:
                n_tls = int(n_tls)
            except (TypeError, ValueError):
                n_tls = 0
            tls_sessions += n_tls
            if d.get("result_type"):
                tls_failures[str(d["result_type"])] += n_tls
            continue
```

Enfin, ajouter les deux clés au dictionnaire renvoyé :

```python
        "tls_sessions": tls_sessions,
        "tls_failures": dict(tls_failures),
```

- [ ] **Step 7: Ajouter le test d'isolation BLOQUANT**

Ajouter à la fin de `backend/tests/test_tenant_isolation.py` :

```python
def test_ip_TLS_vue_par_b_est_invisible_de_a(seed_two_tenants):
    """Même principe que pour une IP DMARC : le contrôle d'appartenance de /ip-intel
    interroge maintenant DEUX champs. Il doit rester aveugle aux lignes des autres.
    """
    from app.db.models import ReportRow

    tid_a, tid_b = seed_two_tenants

    with get_session() as db:
        rep_b = db.query(Report).filter_by(tenant_id=tid_b).first()
        db.add(ReportRow(tenant_id=tid_b, report_id=rep_b.id,
                         data={"kind": "failure", "sending_mta_ip": "198.51.100.77",
                               "result_type": "starttls-not-supported",
                               "failure_sessions": 9}))
        db.commit()

    try:
        with tenant_scoped_session(tenant_id=tid_a) as db:
            vues = (db.query(ReportRow)
                      .filter(ReportRow.data["sending_mta_ip"].astext == "198.51.100.77")
                      .all())
            assert vues == [], "A voit une ligne TLS de B"
    finally:
        with get_session() as db:
            db.query(ReportRow).filter(
                ReportRow.data["sending_mta_ip"].astext == "198.51.100.77").delete(
                synchronize_session=False)
            db.commit()
```

- [ ] **Step 8: Lancer les tests**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" api pytest tests/test_ip_intel_api.py tests/test_tenant_isolation.py -v`
Expected: PASS — 8 + 5 tests

- [ ] **Step 9: Lint + commit**

```bash
git add backend/migrations/versions/0005_tls_ip_index.py backend/app/api/ip_intel.py backend/tests/test_ip_intel_api.py backend/tests/test_tenant_isolation.py
git commit -m "feat(ip-intel): l IP qui echoue en TLS merite la meme enquete

Le controle d appartenance cherchait source_ip ; une ligne TLS porte sending_mta_ip.
Cliquer une IP TLS aurait donne 404 — sur une IP que le tenant voit pourtant dans ses
propres rapports.

On ne renomme PAS le champ pour simplifier : un expediteur evalue par DMARC et un MTA
qui tente une session TLS ne sont pas la meme chose, et le front distingue une ligne
DMARC par la presence de source_ip.

Le resume d activite compte desormais les sessions TLS : sans ca le panneau afficherait
0 message sur une IP qui a bel et bien echoue."
```

---

### Task 5: « Puis-je passer en enforce ? » — l'agrégation et sa route

**Files:**
- Create: `backend/app/services/tls_posture.py`
- Modify: `backend/app/api/admin.py` (ajouter la route après `get_mta_sts`)
- Test: `backend/tests/test_tls_posture.py`

**Interfaces:**
- Consumes: modèle `ReportRow`, `tenant_scoped_session`.
- Produces:
  - `posture(db, days: int = 30) -> dict` — agrège les lignes TLS **visibles dans la session fournie**. Aucun filtre applicatif : c'est la RLS qui scope.
  - Route `GET /admin/tenants/{tenant_id}/tls-posture?days=30`.

Le service ne connaît **pas** le tenant : il reçoit une session déjà scopée. C'est ce qui
le rend testable seul, réutilisable par une future route tenant, et incapable de fuiter.

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `backend/tests/test_tls_posture.py` :

```python
"""« Puis-je passer MTA-STS en enforce sans perdre de courrier ? »

Deux tests portent le poids :
 - `test_pas_de_double_comptage` : le résumé et le détail comptent les mêmes sessions.
 - `test_aucun_rapport_nest_pas_une_preuve_de_succes` : l'erreur qui coûterait cher.
   Un domaine silencieux doit s'entendre dire « on ne sait pas », jamais « c'est sûr ».
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import Email, Report, ReportRow, Tenant
from app.db.session import get_session, tenant_scoped_session
from app.services.tls_posture import posture


@pytest.fixture
def tenant_tls():
    """Un tenant avec un rapport vide. Chaque test y sème les lignes TLS dont il a besoin.
    Renvoie (tenant_id, report_id), tous deux en `str`."""
    with get_session() as db:
        t = Tenant(domain="tls-test.example", name="TLS")
        db.add(t)
        db.flush()
        em = Email(tenant_id=t.id, message_id=f"tls-{uuid.uuid4()}",
                   from_address="noreply@google.com", subject="s",
                   received_at=datetime.now(timezone.utc),
                   raw_object_key="raw/x.eml", status="parsed_ok")
        db.add(em)
        db.flush()
        rep = Report(tenant_id=t.id, email_id=em.id, source_type="attachment", status="ok")
        db.add(rep)
        db.flush()
        ids = (str(t.id), str(em.id), str(rep.id))
        db.commit()

    yield ids[0], ids[2]

    with get_session() as db:
        db.query(ReportRow).filter_by(report_id=ids[2]).delete()
        db.query(Report).filter_by(id=ids[2]).delete()
        db.query(Email).filter_by(id=ids[1]).delete()
        db.query(Tenant).filter_by(id=ids[0]).delete()
        db.commit()


def _seme(tid: str, rid: str, data: dict, jours: int = 1) -> None:
    """Ajoute une ligne TLS datée d'il y a `jours` jours."""
    d = (datetime.now(timezone.utc) - timedelta(days=jours)).date().isoformat()
    with get_session() as db:
        db.add(ReportRow(tenant_id=tid, report_id=rid,
                         data={"reporter": "Google Inc.", "report_date": d,
                               "policy_domain": "tls-test.example", **data}))
        db.commit()


def test_aucun_rapport_nest_pas_une_preuve_de_succes(tenant_tls):
    """L'erreur qui coûterait du courrier : conclure « c'est sûr » d'un silence."""
    tid, _ = tenant_tls

    with tenant_scoped_session(tenant_id=tid) as db:
        p = posture(db, days=30)

    assert p["sessions_total"] == 0
    assert p["safe_to_enforce"] is False       # « on ne sait pas », surtout pas « oui »


def test_sessions_sans_echec_autorisent_enforce(tenant_tls):
    tid, rid = tenant_tls
    _seme(tid, rid, {"kind": "summary", "successful_sessions": 1000,
                     "failed_sessions": 0})

    with tenant_scoped_session(tenant_id=tid) as db:
        p = posture(db, days=30)

    assert p["sessions_ok"] == 1000
    assert p["sessions_failed"] == 0
    assert p["sessions_total"] == 1000
    assert p["failures"] == []
    assert p["safe_to_enforce"] is True
    assert p["reporters"] == ["Google Inc."]


def test_pas_de_double_comptage(tenant_tls):
    """Le résumé dit 3 échecs, le détail détaille ces mêmes 3 échecs. Le total doit être
    3 — pas 6. C'est tout l'intérêt des noms de compteurs distincts."""
    tid, rid = tenant_tls
    _seme(tid, rid, {"kind": "summary", "successful_sessions": 997,
                     "failed_sessions": 3})
    _seme(tid, rid, {"kind": "failure",
                     "result_type": "certificate-host-mismatch",
                     "sending_mta_ip": "203.0.113.5",
                     "receiving_mx_hostname": "mx-backup.tls-test.example",
                     "failure_sessions": 3})

    with tenant_scoped_session(tenant_id=tid) as db:
        p = posture(db, days=30)

    assert p["sessions_failed"] == 3           # PAS 6
    assert p["sessions_total"] == 1000
    assert p["safe_to_enforce"] is False
    assert p["failures"] == [{
        "result_type": "certificate-host-mismatch",
        "sessions": 3,
        "sending_mta_ip": "203.0.113.5",
        "receiving_mx_hostname": "mx-backup.tls-test.example",
    }]


def test_hors_fenetre_est_ignore(tenant_tls):
    tid, rid = tenant_tls
    _seme(tid, rid, {"kind": "summary", "successful_sessions": 10,
                     "failed_sessions": 5}, jours=90)

    with tenant_scoped_session(tenant_id=tid) as db:
        p = posture(db, days=30)

    assert p["sessions_total"] == 0
    assert p["safe_to_enforce"] is False
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" api pytest tests/test_tls_posture.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.tls_posture'`

- [ ] **Step 3: Écrire le service**

Créer `backend/app/services/tls_posture.py` :

```python
"""« Puis-je passer MTA-STS en enforce sans perdre de courrier ? »

C'est LA question que les rapports TLS existent pour trancher, et la seule qui compte
devant le sélecteur de mode : en `enforce`, un expéditeur qui n'arrive pas à valider le
certificat **cesse de livrer** — sans alerte, sans trace de notre côté.

Deux pièges, tous deux mortels, tous deux évités ici :

 - **Ne pas double-compter les échecs.** Le résumé d'une politique dit « 3 échecs » ; le
   détail décrit ces mêmes 3 échecs. Sommer les deux donnerait 6. Les champs portent donc
   des noms distincts (`failed_sessions` / `failure_sessions`) : le total vient TOUJOURS
   des lignes `summary`, le détail ne sert qu'à dire quoi et qui.

 - **Ne pas confondre silence et succès.** Un domaine dont on n'a reçu aucun rapport n'est
   pas un domaine sans échec : c'est un domaine sur lequel on ne sait rien. Y répondre
   « c'est sûr » ferait durcir à l'aveugle — exactement ce que TLS-RPT sert à éviter.
   `safe_to_enforce` exige donc des données ET aucun échec.

Le service ne connaît pas le tenant : il reçoit une session **déjà scopée**. C'est ce qui
le rend testable seul et incapable de fuiter — aucun `WHERE tenant_id` applicatif, la RLS
fait le travail (CLAUDE.md).
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from sqlalchemy import Integer, cast

from app.db.models import ReportRow

_kind = ReportRow.data["kind"].astext
_report_date = ReportRow.data["report_date"].astext


def posture(db, days: int = 30) -> dict:
    cutoff = (date.today() - timedelta(days=days)).isoformat()

    rows = (db.query(ReportRow)
              .filter(_kind.in_(("summary", "failure")))
              .filter(_report_date >= cutoff)
              .all())

    sessions_ok = 0
    sessions_failed = 0
    reporters: set[str] = set()

    # Un échec est identifié par (type, MTA émetteur, MX visé) : c'est ce triplet qui dit
    # à l'exploitant quoi corriger. Deux rapports différents décrivant le même problème
    # doivent s'additionner, pas se dupliquer.
    detail: Counter[tuple[str, str, str]] = Counter()

    for r in rows:
        d = r.data
        if d.get("reporter"):
            reporters.add(str(d["reporter"]))

        if d.get("kind") == "summary":
            sessions_ok += _int(d.get("successful_sessions"))
            sessions_failed += _int(d.get("failed_sessions"))
            continue

        key = (str(d.get("result_type") or "inconnu"),
               str(d.get("sending_mta_ip") or ""),
               str(d.get("receiving_mx_hostname") or ""))
        detail[key] += _int(d.get("failure_sessions"))

    failures = [
        {"result_type": rt, "sessions": n,
         "sending_mta_ip": ip or None, "receiving_mx_hostname": mx or None}
        for (rt, ip, mx), n in detail.most_common()
    ]

    total = sessions_ok + sessions_failed

    return {
        "days": days,
        "sessions_total": total,
        "sessions_ok": sessions_ok,
        "sessions_failed": sessions_failed,
        "failures": failures,
        # Des données ET aucun échec. Le silence n'est pas une preuve.
        "safe_to_enforce": total > 0 and sessions_failed == 0,
        "reporters": sorted(reporters),
    }


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
```

Note : `cast` et `Integer` ne sont pas utilisés dans cette version (l'agrégation se fait
en Python, sur un volume de lignes TLS très faible — quelques dizaines par mois et par
domaine, contre des milliers pour DMARC). Supprimer l'import
`from sqlalchemy import Integer, cast` s'il déclenche une erreur `ruff` F401.

- [ ] **Step 4: Ajouter la route admin**

Dans `backend/app/api/admin.py`, ajouter l'import :

```python
from app.services.tls_posture import posture
```

Puis, juste après la route `get_mta_sts` (vers la ligne 165), ajouter :

```python
@router.get("/tenants/{tenant_id}/tls-posture")
def tenant_tls_posture(tenant_id: str, days: int = 30):
    """Les rapports TLS de CE domaine — la seule chose qui permette de décider d'un
    passage en `enforce` sans durcir à l'aveugle.

    Session scopée par la RLS sur ce tenant précis (pas de bypass, pas de `WHERE`
    applicatif) : même un platform_admin ne peut pas lire les lignes d'un autre domaine
    par cette route. C'est l'option la plus restrictive, et elle ne coûte rien.
    """
    with tenant_scoped_session(tenant_id=tenant_id) as db:
        return posture(db, days=days)
```

- [ ] **Step 5: Lancer les tests**

Run: `MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" api pytest tests/test_tls_posture.py -q`
Expected: PASS (4 tests)

- [ ] **Step 6: Lint + suite complète + commit**

```bash
cd infra && docker compose run --rm --no-deps -v "D:/code/dmarc/backend:/app" api sh -c "pytest -q && ruff check app"
```

```bash
git add backend/app/services/tls_posture.py backend/app/api/admin.py backend/tests/test_tls_posture.py
git commit -m "feat(tls): puis-je passer en enforce ? — l agregation qui repond

Deux pieges, tous deux mortels. Ne pas double-compter : le resume dit 3 echecs, le
detail decrit ces memes 3 echecs — sommer les deux donnerait 6. Le total vient donc
TOUJOURS des lignes summary.

Et ne pas confondre silence et succes : un domaine sans rapport TLS n est pas un
domaine sans echec, c est un domaine sur lequel on ne sait rien. safe_to_enforce exige
des donnees ET aucun echec.

La session est scopee par la RLS sur ce tenant precis, sans bypass : meme un
platform_admin ne lit pas les lignes d un autre domaine par cette route."
```

---

### Task 6: L'écran — le verdict là où se prend la décision

**Files:**
- Modify: `frontend/src/api/domains.ts` (ajouter `useTlsPosture`)
- Modify: `frontend/src/components/MtaStsPanel.tsx` (insérer le verdict avant le `fieldset` des modes)
- Modify: `frontend/src/pages/ReportDetail.tsx` (rendu des lignes TLS)

**Interfaces:**
- Consumes: `GET /admin/tenants/{id}/tls-posture` (Task 5), `IpPanel` (existant).
- Produces: rien (feuille de l'arbre).

- [ ] **Step 1: Ajouter le hook**

Dans `frontend/src/api/domains.ts`, ajouter (suivre la forme des hooks existants du
fichier, qui utilisent `api` et `useQuery`) :

```typescript
export interface TlsFailure {
  result_type: string;
  sessions: number;
  sending_mta_ip: string | null;
  receiving_mx_hostname: string | null;
}

export interface TlsPosture {
  days: number;
  sessions_total: number;
  sessions_ok: number;
  sessions_failed: number;
  failures: TlsFailure[];
  safe_to_enforce: boolean;
  reporters: string[];
}

export const useTlsPosture = (tenantId: string) =>
  useQuery({
    queryKey: ["tls-posture", tenantId],
    queryFn: () => api<TlsPosture>(`/admin/tenants/${tenantId}/tls-posture`),
  });
```

- [ ] **Step 2: Afficher le verdict dans le panneau MTA-STS**

Dans `frontend/src/components/MtaStsPanel.tsx` :

Compléter l'import :

```typescript
import { useMtaSts, useSaveMtaSts, useTlsPosture } from "../api/domains";
```

Dans le composant, après `const save = useSaveMtaSts(tenantId);` :

```typescript
  const tls = useTlsPosture(tenantId);
```

Insérer le bloc **juste avant** `<fieldset className="space-y-2">` (le sélecteur de mode) —
c'est là que la décision se prend, donc c'est là que la preuve doit être :

```tsx
        {tls.data && <TlsVerdict p={tls.data} />}
```

Et ajouter le composant en fin de fichier :

```tsx
/* Le verdict TLS se lit JUSTE AVANT le sélecteur de mode, parce que c'est exactement là
   que se prend la décision qu'il éclaire. Une page séparée qu'il faut penser à ouvrir ne
   servirait personne.

   Trois états, et le premier est le plus important : ne RIEN savoir n'est pas rassurant.
   Un domaine silencieux n'est pas un domaine sans échec — c'est un domaine sur lequel on
   n'a aucune donnée. Le dire autrement ferait durcir à l'aveugle, ce que TLS-RPT sert
   précisément à éviter. */
function TlsVerdict({ p }: { p: TlsPosture }) {
  if (p.sessions_total === 0) {
    return (
      <div className="rounded border border-gray-300 bg-gray-50 p-3 text-xs text-gray-700">
        <strong>Aucun rapport TLS reçu sur {p.days} jours.</strong> On ne sait donc pas si
        le chiffrement fonctionne — ce n'est pas la même chose que « tout va bien ».
        Publiez l'enregistrement <code className="font-mono">_smtp._tls</code> (voir la
        procédure du domaine) avant de durcir, sinon vous durcirez à l'aveugle.
      </div>
    );
  }

  if (p.safe_to_enforce) {
    return (
      <div className="rounded border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-900">
        <strong>
          {p.sessions_ok.toLocaleString("fr-FR")} sessions sur {p.days} jours, toutes
          chiffrées, aucun échec.
        </strong>{" "}
        Le passage en mode appliqué est sûr.
        {p.reporters.length > 0 && (
          <span className="block mt-1 text-emerald-800">
            D'après : {p.reporters.join(", ")}.
          </span>
        )}
      </div>
    );
  }

  return (
    <div className="rounded border border-red-300 bg-red-50 p-3 text-xs text-red-900">
      <strong>
        {p.sessions_failed.toLocaleString("fr-FR")} session
        {p.sessions_failed > 1 ? "s" : ""} en échec de chiffrement sur {p.days} jours
      </strong>{" "}
      (sur {p.sessions_total.toLocaleString("fr-FR")}). En mode appliqué, ces messages
      seraient <strong>refusés</strong>. Corrigez d'abord.
      <ul className="mt-2 space-y-1">
        {p.failures.map((f, i) => (
          <li key={i} className="font-mono">
            {f.result_type} · {f.sessions} session{f.sessions > 1 ? "s" : ""}
            {f.sending_mta_ip && <> · depuis {f.sending_mta_ip}</>}
            {f.receiving_mx_hostname && <> · vers {f.receiving_mx_hostname}</>}
          </li>
        ))}
      </ul>
    </div>
  );
}
```

Ajouter `type TlsPosture` à l'import depuis `../api/domains` :

```typescript
import { type TlsPosture, useMtaSts, useSaveMtaSts, useTlsPosture } from "../api/domains";
```

- [ ] **Step 3: Rendre les lignes TLS lisibles, et l'IP émettrice cliquable**

Dans `frontend/src/pages/ReportDetail.tsx`, fonction `RowsTable` : la détection actuelle
est `const isDmarc = "source_ip" in rows[0];`. La compléter :

```tsx
  // Chaque famille se reconnaît à ses DONNÉES, pas à un nom de profil : `Report` ne
  // stocke pas le format, seulement source_type (attachment/body) et profile_id.
  const isDmarc = "source_ip" in rows[0];
  const isTls = "kind" in rows[0] && "policy_domain" in rows[0];
```

Et le rendu :

```tsx
      {isDmarc ? (
        <DmarcTable rows={rows} onSelectIp={setIp} />
      ) : isTls ? (
        <TlsTable rows={rows} onSelectIp={setIp} />
      ) : (
        <GenericTable rows={rows} />
      )}
```

Ajouter le composant (après `DmarcTable`) :

```tsx
/** Un rapport TLS mêle deux natures de lignes : le bilan chiffré d'une politique, et le
 *  détail de chaque échec. Les afficher pêle-mêle dans une table à colonnes fixes
 *  produirait une forêt de tirets. On les sépare. */
function TlsTable({
  rows,
  onSelectIp,
}: {
  rows: Record<string, unknown>[];
  onSelectIp: (ip: string) => void;
}) {
  const summaries = rows.filter((r) => r.kind === "summary");
  const failures = rows.filter((r) => r.kind === "failure");

  return (
    <div className="space-y-6">
      {summaries.length > 0 && (
        <div>
          <h3 className="mb-2 text-xs uppercase tracking-wide text-gray-400">Sessions</h3>
          <table className="w-full text-sm">
            <thead className="border-b text-left text-gray-500">
              <tr>
                <th className="py-2 pr-4">Politique</th>
                <th className="py-2 pr-4">Serveurs couverts</th>
                <th className="py-2 pr-4">Chiffrées</th>
                <th className="py-2 pr-4">En échec</th>
              </tr>
            </thead>
            <tbody>
              {summaries.map((r, i) => (
                <tr key={i} className="border-b">
                  <td className="py-1 pr-4">{String(r.policy_type ?? "—")}</td>
                  <td className="py-1 pr-4 font-mono text-xs">{String(r.mx_host ?? "—")}</td>
                  <td className="py-1 pr-4 text-green-700">
                    {String(r.successful_sessions ?? "—")}
                  </td>
                  <td className="py-1 pr-4 text-red-700">
                    {String(r.failed_sessions ?? "—")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {failures.length > 0 && (
        <div>
          <h3 className="mb-2 text-xs uppercase tracking-wide text-gray-400">
            Échecs de chiffrement
          </h3>
          <table className="w-full text-sm">
            <thead className="border-b text-left text-gray-500">
              <tr>
                <th className="py-2 pr-4">Type d'échec</th>
                <th className="py-2 pr-4">Sessions</th>
                <th className="py-2 pr-4">MTA émetteur</th>
                <th className="py-2 pr-4">Serveur visé</th>
              </tr>
            </thead>
            <tbody>
              {failures.map((r, i) => (
                <tr key={i} className="border-b">
                  <td className="py-1 pr-4">{String(r.result_type ?? "—")}</td>
                  <td className="py-1 pr-4">{String(r.failure_sessions ?? "—")}</td>
                  <td className="py-1 pr-4">
                    {r.sending_mta_ip ? (
                      // Une IP qui échoue en TLS mérite la même enquête qu'une IP rejetée
                      // en DMARC : c'est le même panneau.
                      <button
                        onClick={() => onSelectIp(String(r.sending_mta_ip))}
                        className="font-mono text-blue-600 hover:underline"
                      >
                        {String(r.sending_mta_ip)}
                      </button>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="py-1 pr-4 font-mono text-xs">
                    {String(r.receiving_mx_hostname ?? "—")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Vérifier la compilation TypeScript**

Run: `cd infra && MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/frontend/src:/app/src" frontend npx tsc -b`
Expected: aucune sortie (succès)

- [ ] **Step 5: Vérifier en vrai — le pipeline entier, pas seulement le build**

Un test unitaire ne prouve pas que le worker route correctement une vraie pièce jointe.
On fabrique donc un vrai `.eml` avec un vrai rapport TLS gzippé, et on le fait entrer par
la porte d'entrée réelle — `IngestionService` — puis traiter par `process_email`.

```bash
cd infra && docker compose up --build -d api worker frontend
docker compose exec api python -m scripts.seed
```

```bash
docker compose exec -T api python - <<'EOF'
import gzip, json, uuid
from email.message import EmailMessage

from app.config import settings
from app.db.models import Email, Report, ReportRow
from app.db.session import get_session
from app.ingestion.service import IngestionService, IngestSource
from app.storage import ObjectStore
from app.workers.tasks import process_email

rapport = {
    "organization-name": "Google Inc.",
    "date-range": {"start-datetime": "2026-07-13T00:00:00Z",
                   "end-datetime": "2026-07-13T23:59:59Z"},
    "contact-info": "smtp-tls-reporting@google.com",
    "report-id": "2026-07-13T00:00:00Z_acme.com",
    "policies": [{
        "policy": {"policy-type": "sts", "policy-domain": "acme.com",
                   "mx-host": ["*.mail.protection.outlook.com"]},
        "summary": {"total-successful-session-count": 1000,
                    "total-failure-session-count": 3},
        "failure-details": [{"result-type": "certificate-host-mismatch",
                             "sending-mta-ip": "209.85.220.41",
                             "receiving-mx-hostname": "mx-backup.acme.com",
                             "failed-session-count": 3}],
    }],
}

msg = EmailMessage()
msg["From"] = "noreply-smtp-tls-reporting@google.com"
msg["Message-ID"] = f"<tls-{uuid.uuid4()}@google.com>"
# Le sujet réel d'un rapport TLS-RPT : c'est lui qui résout le tenant, sans règle nouvelle.
msg["Subject"] = "Report Domain: acme.com Submitter: google.com Report-ID: <2026.07.13>"
msg.set_content("Rapport TLS-RPT en pièce jointe.")
# Nom réel, extension .gz : c'est EXACTEMENT ce qui cassait.
msg.add_attachment(gzip.compress(json.dumps(rapport).encode()),
                   maintype="application", subtype="tlsrpt+gzip",
                   filename="google.com!acme.com!1752!1752.json.gz")

res = IngestionService(ObjectStore.from_settings(settings)).ingest(
    msg.as_bytes(), IngestSource(kind="imap", detail="verif"))
print("ingest:", res.status, res.email_id)

process_email(res.email_id)          # synchrone : on veut voir le résultat tout de suite

with get_session() as db:
    em = db.get(Email, res.email_id)
    rep = db.query(Report).filter_by(email_id=em.id).first()
    rows = db.query(ReportRow).filter_by(report_id=rep.id).all() if rep else []
    print("statut e-mail :", em.status)          # attendu : parsed_ok
    print("statut rapport:", rep.status if rep else None, "| lignes:", len(rows))
    for r in rows:
        print("  ", r.data.get("kind"), "|", {k: v for k, v in r.data.items()
                                              if k in ("successful_sessions",
                                                       "failed_sessions",
                                                       "result_type",
                                                       "sending_mta_ip",
                                                       "failure_sessions")})
EOF
```

Attendu : `statut e-mail : parsed_ok`, un rapport `ok`, **2 lignes** — une `summary`
(1000 / 3) et une `failure` (`certificate-host-mismatch`, 3, depuis `209.85.220.41`).
Si le statut est `failed` avec `DMARC_BAD_XML`, la détection par contenu n'est pas
branchée.

Puis, dans l'interface (http://localhost:5173, `user@acme.com` / `acme`) :

- le rapport apparaît en `ok`, et son détail affiche « Sessions » et « Échecs de
  chiffrement » — pas du JSON brut ;
- l'IP `209.85.220.41` est **cliquable** et ouvre le panneau d'enquête, avec l'activité TLS
  comptée (3 sessions, `certificate-host-mismatch`) ;
- page Domaines → `acme.com` → MTA-STS : le verdict rouge « 3 sessions en échec » apparaît
  **au-dessus** du sélecteur de mode ;
- page Domaines → `globex.com` (aucun rapport TLS) : « on ne sait pas », **pas** un feu
  vert. C'est le cas qui compte le plus.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/domains.ts frontend/src/components/MtaStsPanel.tsx frontend/src/pages/ReportDetail.tsx
git commit -m "feat(front): le verdict TLS s affiche la ou se prend la decision

Juste au-dessus du selecteur de mode MTA-STS : c est le seul endroit ou la question
puis-je passer en enforce ? se pose reellement. Une page separee qu il faut penser a
ouvrir n aurait servi personne.

L etat le plus important est le premier : aucun rapport recu ne dit pas tout va bien,
il dit on ne sait pas. Les confondre ferait durcir a l aveugle — exactement ce que
TLS-RPT sert a eviter."
```

---

## Vérification finale

- [ ] `cd infra && docker compose exec api pytest` — suite complète verte
- [ ] `docker compose exec api pytest tests/test_tenant_isolation.py -v` — **bloquant**, 5 tests
- [ ] `docker compose exec api ruff check app` — `All checks passed!`
- [ ] `docker compose run --rm --no-deps -v "D:/code/dmarc/frontend/src:/app/src" frontend npx tsc -b` — vert
- [ ] Parcours réel : un rapport TLS traverse le pipeline → `parsed_ok` → lignes lisibles → IP cliquable → verdict dans le panneau MTA-STS

## Récupérer l'historique

Les rapports TLS déjà reçus sont en `failed` (`DMARC_BAD_XML`) — mais le `.eml` brut est
en S3, par invariant. Une fois le pipeline corrigé, ils se relisent :

```bash
docker compose exec api python -c "
from app.db.models import Email, ParsingError
from app.db.session import get_session
from app.workers.tasks import reprocess_report

with get_session() as db:
    ids = [str(e.email_id) for e in db.query(ParsingError.email_id)
           .filter(ParsingError.code == 'DMARC_BAD_XML').distinct()]
print(len(ids), 'e-mails a rejouer')
for i in ids:
    reprocess_report.delay(i)
"
```

À faire **après** la vérification finale, et à annoncer : ce n'est pas une étape de code,
c'est une opération sur des données réelles.

## Ce que ce plan ne fait PAS, délibérément

- **L'alerte.** Sous-système à part entière : canal, destinataire, seuil, déduplication.
  Et le seuil pertinent ne se choisit qu'en regardant de **vrais** rapports — qu'on n'a pas
  encore. Cycle dédié, après celui-ci.
- **Le décodage des `policy-string`.** On les reçoit, on ne les stocke même pas : elles ne
  servent à aucune décision.
- **Une page « chiffrement » dédiée.** Le verdict va là où se prend la décision.
