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
    s3_endpoint: str = "http://minio:9000"
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
    jwt_issuer: str = "report-platform"
    jwt_audience: str = "report-dashboard"
    jwt_ttl_seconds: int = 3600

    # --- Observabilité ---
    sentry_dsn: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
