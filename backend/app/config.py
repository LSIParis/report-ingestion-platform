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

    # --- Observabilité ---
    sentry_dsn: str = ""

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


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
