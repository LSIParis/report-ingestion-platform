from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- DB : deux rôles distincts (isolation en profondeur) ---
    database_url_api: str = "postgresql://app_api:app_api@localhost:5432/reports"
    database_url_worker: str = "postgresql://app_worker:app_worker@localhost:5432/reports"
    database_url_migrate: str = ""

    # --- File / broker ---
    redis_url: str = "redis://redis:6379/0"

    # --- Object store (S3/MinIO) ---
    s3_endpoint: str = "http://minio:9000"          # endpoint INTERNE (api/worker → minio)
    s3_public_endpoint: str = ""                     # endpoint PUBLIC pour les URLs signées
                                                     # (joignable par le navigateur). Vide → =s3_endpoint
    s3_bucket: str = "reports-raw"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_region: str = "us-east-1"

    # --- Ingestion IMAP (dev) ---
    imap_host: str = ""
    imap_user: str = ""
    imap_password: str = ""

    # --- JWT (RS256) ---
    jwt_public_key: str = ""
    jwt_private_key: str = ""
    jwt_public_key_file: str = ""      # prod : chemin d'un fichier PEM monté (prioritaire si présent)
    jwt_private_key_file: str = ""
    # Portainer écrit les variables dans un stack.env (une ligne par variable) : une
    # valeur multiligne comme un PEM y est impossible. Variante base64, sur une ligne.
    jwt_public_key_b64: str = ""
    jwt_private_key_b64: str = ""
    jwt_issuer: str = "report-platform"
    jwt_audience: str = "report-dashboard"
    jwt_ttl_seconds: int = 3600

    # --- Antivirus (ClamAV) ---
    antivirus_enabled: bool = False        # actif en prod ; no-op si False
    clamav_host: str = "clamav"
    clamav_port: int = 3310

    # --- Procédure d'ajout d'un domaine (vérification DNS en direct) ---
    # Adresse où les domaines surveillés doivent envoyer leurs rapports. Par défaut la
    # boîte relevée par l'ingestion : les deux ne peuvent pas diverger.
    collection_mailbox: str = ""
    # Les rapports TLS-RPT arrivent dans une boîte DISTINCTE de celle des rapports DMARC :
    # ce sont deux flux différents, avec deux enregistrements DNS différents.
    tlsrpt_mailbox: str = ""
    # Domaine qui doit publier les autorisations de collecte externe (X._report._dmarc).
    reporting_domain: str = ""
    # IP de l'hôte qui sert les politiques MTA-STS (enregistrement A `mta-sts.<domaine>`).
    mta_sts_ip: str = ""

    # --- Résolution DNS de l'enrichissement des IP ---
    # Serveurs interrogés pour le PTR, l'ASN (Team Cymru) et le SPF. On NE se fie PAS au
    # résolveur du conteneur : celui de Docker (127.0.0.11) ne relaie pas les requêtes
    # PTR pour les IP publiques — il répond « NoAnswer ». Le reverse DNS serait donc
    # toujours vide, et avec lui le FCrDNS : plus aucun expéditeur ne serait identifié,
    # sans la moindre erreur pour le signaler.
    dns_resolvers: str = "1.1.1.1,8.8.8.8"

    # --- Observabilité ---
    sentry_dsn: str = ""

    # --- Alertes ---
    # URL générique : un POST JSON. Aucun couplage à un fournisseur (n8n, un script, un
    # endpoint à vous). Vide → aucun envoi, mais les alertes s'ouvrent quand même en base
    # et l'absence d'envoi est JOURNALISÉE. On n'avale jamais une alerte en silence.
    alert_webhook_url: str = ""
    # Jours sans le moindre rapport avant de déclarer un domaine silencieux. Un domaine à
    # très faible trafic peut légitimement passer quelques jours sans rapport : on accepte
    # ce bruit, délibérément. Un faux positif coûte un coup d'œil ; un faux négatif laisse
    # un client sans protection pendant des mois.
    alert_silence_days: int = 4
    # Délai laissé à un nouveau domaine pour publier son DMARC avant qu'on s'en inquiète.
    alert_onboarding_grace_days: int = 7

    def model_post_init(self, __context) -> None:
        """Résout les clés JWT. Priorité : fichier monté > base64 > valeur brute."""
        import base64
        from pathlib import Path

        for name in ("jwt_public_key", "jwt_private_key"):
            path = getattr(self, f"{name}_file")
            if path and Path(path).is_file():
                object.__setattr__(self, name, Path(path).read_text(encoding="utf-8"))
                continue
            b64 = getattr(self, f"{name}_b64")
            if b64:
                object.__setattr__(
                    self, name, base64.b64decode(b64).decode("utf-8"))

        # La boîte de collecte annoncée aux clients EST celle qu'on relève : les faire
        # diverger produirait une procédure qui envoie les rapports dans le vide.
        if not self.collection_mailbox and self.imap_user:
            object.__setattr__(self, "collection_mailbox", self.imap_user)
        if not self.reporting_domain and "@" in self.collection_mailbox:
            object.__setattr__(self, "reporting_domain",
                               self.collection_mailbox.split("@", 1)[1])


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
