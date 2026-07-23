from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, loaded from environment / .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://o2_admin:o2_secret@localhost:5432/projecto2"
    secret_key: str = "dev-secret-change-me"
    access_token_expire_minutes: int = 720
    algorithm: str = "HS256"
    frontend_origin: str = "http://localhost:3000"
    upload_dir: str = "./uploads"

    # Object storage (Amazon S3). If aws_s3_bucket is set, uploads are written to
    # s3://<bucket>/<prefix> and downloads are served via presigned URLs;
    # otherwise files are written to upload_dir on local disk. Credentials fall
    # back to the standard AWS chain when the explicit keys are left blank.
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "ap-south-1"
    aws_s3_bucket: str = ""
    aws_s3_prefix: str = "o2/"

    app_name: str = "Project O2 API"
    api_v1_prefix: str = "/api"

    # Only emails on this domain may sign up or be invited (the workspace).
    workspace_email_domain: str = "optiminastic.com"

    # Outgoing email (team invitations). If smtp_host is blank, sending is
    # skipped gracefully and the invite link is surfaced in the UI / logs instead.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_from_name: str = "Project O2"
    smtp_reply_to: str = ""
    smtp_use_tls: bool = True
    invite_expire_hours: int = 168  # 7 days

    # GST verification (Appyflow). When gst_api_key is blank the /gst endpoint
    # still validates the number and derives PAN + state offline; the legal /
    # trade name and registered address are only fetched when a key is present.
    gst_api_key: str = ""
    gst_api_url: str = "https://appyflow.in/api/verifyGST"

    @field_validator("database_url", mode="after")
    @classmethod
    def _normalize_db_scheme(cls, v: str) -> str:
        # Render / Heroku hand out URLs starting with `postgres://` or
        # `postgresql://`. Our stack uses psycopg3, which needs the explicit
        # `postgresql+psycopg://` driver scheme — rewrite it if missing.
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://") :]
        if v.startswith("postgresql://"):
            v = "postgresql+psycopg://" + v[len("postgresql://") :]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
