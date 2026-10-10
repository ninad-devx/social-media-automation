"""Application configuration.

All environment-specific values live here and are read from the environment
(or a local .env file). Nothing in this module is ever exposed to the frontend.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Application -----------------------------------------------------
    app_name: str = "Social Media Automation"
    app_env: str = "development"
    debug: bool = True
    api_prefix: str = "/api"

    # Comma-separated list. Never use ["*"] outside local development.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173"

    # ---- Database --------------------------------------------------------
    # Defaults to a local SQLite file so the MVP needs no extra infrastructure.
    # Point this at PostgreSQL for production, e.g.
    #   postgresql+psycopg://user:pass@localhost:5432/social
    database_url: str = "sqlite:///./social_media.db"
    sql_echo: bool = False

    # ---- n8n publishing engine -----------------------------------------
    # Full production webhook URL of the existing n8n workflow, e.g.
    #   http://localhost:5678/webhook/social/publish
    n8n_publish_webhook_url: str = "http://localhost:5678/webhook/social/publish"
    # Optional shared secret. Sent as X-N8N-Secret when set. Never leaves the backend.
    n8n_webhook_secret: str | None = None
    n8n_timeout_seconds: float = 60.0
    n8n_verify_tls: bool = True

    # ---- Publishing engine selection ------------------------------------
    # "n8n"    : hand the request to the n8n webhook (unchanged, still default
    #            so an existing local install keeps working untouched).
    # "direct" : the backend resolves the credential and calls Buffer itself.
    #            Fewer hops, no n8n dependency at publish time.
    # Either way the same single generic workflow JSON is used, and the n8n
    # workflow never needs per-account configuration.
    publish_engine: str = "n8n"

    # ---- Buffer API ------------------------------------------------------
    # Buffer's supported API is GraphQL over a single endpoint. The legacy
    # api.bufferapp.com REST API is deprecated and is deliberately not used.
    buffer_api_base_url: str = "https://api.buffer.com"
    buffer_timeout_seconds: float = 30.0

    # ---- Internal service-to-service auth --------------------------------
    # Shared secret the n8n workflow presents on the internal publish endpoint
    # (/api/internal/publish). Leave empty to disable that endpoint entirely -
    # it is only needed when PUBLISH_ENGINE=n8n and the workflow performs the
    # Buffer call itself.
    # Generate with: python -c "import secrets;print(secrets.token_urlsafe(32))"
    internal_service_secret: str | None = None

    # Base URL the n8n workflow uses to call back into this backend. Must be
    # reachable FROM the n8n runtime, which is not the same thing as reachable
    # from your browser:
    #   n8n on the same host as the API -> http://host.docker.internal:8000
    #   n8n in the same compose network -> http://backend:8000
    #   n8n in the cloud                -> https://api.example.com
    n8n_backend_base_url: str = "http://localhost:8000"

    # ---- Optional API auth ---------------------------------------------
    # Leave empty for local development. Set to enable header auth for /api.
    api_key: str | None = None

    # ---- Publishing defaults -------------------------------------------
    default_timezone: str = "Asia/Dhaka"
    # Guards against scheduling further out than this many days.
    max_schedule_days_ahead: int = 365

    # ---- AI providers ----------------------------------------------------
    # Fernet key used to encrypt provider API keys at rest.
    # Generate one with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Without it, provider keys cannot be stored (the app never falls back to
    # plain text).
    ai_encryption_key: str | None = None

    # ---- Shared credential encryption ------------------------------------
    # Encrypts EVERY stored secret: AI provider keys AND integration
    # credentials such as Buffer tokens. Kept separate from AI_ENCRYPTION_KEY
    # so either can be rotated independently.
    #
    # If unset, AI_ENCRYPTION_KEY is used as a fallback, which keeps installs
    # created before this key existed working with no edits. Set it explicitly
    # for new deployments.
    credential_encryption_key: str | None = None
    # Default timeout for a provider generation call, in seconds.
    ai_timeout_seconds: float = 90.0
    # How many channels may be generated concurrently in one campaign.
    ai_max_concurrency: int = 4
    # Hard ceiling on how many channels one campaign may target.
    ai_max_channels_per_campaign: int = 60
    # Ceiling on how many campaigns may be generated per hour per instance.
    ai_generations_per_hour: int = 120
    # Uploaded files are written here.
    media_storage_dir: str = "./media"
    # The URL prefix Buffer will use to DOWNLOAD the uploaded file.
    #
    # IMPORTANT: Buffer is a cloud service. It fetches media over the public
    # internet, so this MUST be a publicly reachable URL. A localhost value
    # will validate and store the file but Buffer will not be able to
    # download it. For local development put a tunnel in front of the API,
    # e.g.  cloudflared tunnel --url http://localhost:8000
    # and set this to https://<subdomain>.trycloudflare.com/api/media
    public_media_base_url: str = ""
    # Used to build absolute media URLs when PUBLIC_MEDIA_BASE_URL is empty,
    # so the value handed to the publishing engine is still a valid http(s) URL.
    public_api_base_url: str = "http://localhost:8000"
    # URL the BROWSER uses for thumbnails and previews.
    #
    # Deliberately separate from PUBLIC_MEDIA_BASE_URL: a tunnel such as ngrok
    # serves an interstitial HTML page to browser requests, which would render
    # as a broken thumbnail, while still serving the real file to Buffer. The
    # browser can always reach the API directly, so previews point here.
    preview_media_base_url: str = "http://localhost:8000/api/media"
    # Hard ceiling for a single upload (bytes). Default 25 MiB.
    max_upload_bytes: int = 25 * 1024 * 1024

    # ---- object storage (recommended for publishing media) ---------------
    # "local" serves files from this machine and depends on a tunnel for
    # Buffer to reach them. "s3" writes to any S3-compatible bucket, which
    # gives stable, permanently public URLs.
    media_storage_backend: str = "local"

    # Bucket name, e.g. "pulse-media".
    s3_bucket: str = ""
    # Cloudflare R2: https://<account>.r2.cloudflarestorage.com
    # AWS S3:      https://s3.<region>.amazonaws.com
    # MinIO:       http://localhost:9000
    s3_endpoint: str = ""
    # R2 uses "auto"; AWS uses its region, e.g. "eu-west-1".
    s3_region: str = "auto"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    # Public base the uploaded object is served from. R2:
    # https://pub-<account>.r2.dev  (or a custom domain on your own bucket).
    s3_public_base_url: str = ""

    @field_validator("default_timezone")
    @classmethod
    def _valid_timezone(cls, v: str) -> str:
        import zoneinfo

        try:
            zoneinfo.ZoneInfo(v)
        except Exception as exc:  # pragma: no cover - config error path
            raise ValueError(f"default_timezone '{v}' is not a valid IANA timezone") from exc
        return v

    @field_validator("publish_engine")
    @classmethod
    def _valid_engine(cls, v: str) -> str:
        v = (v or "n8n").strip().lower()
        if v not in ("n8n", "direct"):
            raise ValueError("publish_engine must be 'n8n' or 'direct'")
        return v

    @field_validator("buffer_api_base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return (v or "").rstrip("/")

    @property
    def effective_encryption_key(self) -> str | None:
        """The Fernet key used for every stored secret.

        Falls back to AI_ENCRYPTION_KEY so an existing .env keeps working, and
        so rotating one does not silently invalidate the other.
        """
        return self.credential_encryption_key or self.ai_encryption_key

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in ("production", "prod")

    @property
    def uses_direct_publishing(self) -> bool:
        return self.publish_engine == "direct"

    @property
    def internal_publish_path(self) -> str:
        return f"{self.api_prefix}/internal/publish"

    @property
    def internal_publish_url(self) -> str:
        """Absolute URL the n8n workflow calls back into."""
        return f"{self.n8n_backend_base_url.rstrip('/')}{self.internal_publish_path}"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def media_path(self) -> Path:
        return Path(self.media_storage_dir).expanduser().resolve()

    @property
    def media_base_url(self) -> str:
        """Public prefix used to build the URL handed to Buffer."""
        if self.public_media_base_url:
            return self.public_media_base_url.rstrip("/")
        # Fall back to this service's own address so the value is always an
        # absolute http(s) URL. It will still not be reachable from Buffer.
        return f"{self.public_api_base_url.rstrip('/')}/api/media"

    # ------------------------------------------------------------------ #
    # Startup validation
    # ------------------------------------------------------------------ #
    def startup_problems(self) -> list[str]:
        """Configuration faults that must stop a PRODUCTION boot.

        Only consulted when APP_ENV is production/prod, so local development
        and the test suite are completely unaffected.

        Each entry names the environment variable to set, because "failed to
        start" with no explanation is the least useful thing a deploy can do.
        """
        problems: list[str] = []

        if self.is_sqlite:
            problems.append(
                "DATABASE_URL still points at SQLite ('"
                f"{self.database_url}'). Every deploy on an ephemeral platform "
                "(Render, Fly, Heroku) would start from an empty database and "
                "silently discard all accounts, channels and history. Set "
                "DATABASE_URL to a hosted PostgreSQL instance, for example "
                "postgresql+psycopg://USER:PASSWORD@HOST:5432/DB."
            )

        if not self.effective_encryption_key:
            problems.append(
                "CREDENTIAL_ENCRYPTION_KEY is not set, so no Buffer token or AI "
                "provider key can be stored - Settings will refuse to save them. "
                'Generate one with: python -c "from cryptography.fernet import '
                'Fernet; print(Fernet.generate_key().decode())"'
            )

        if not self.api_key:
            problems.append(
                "API_KEY is not set, so every /api route is publicly writable: "
                "anyone could publish posts, rotate credentials and read your "
                "account data. Set API_KEY, or place real authentication in "
                "front of the service."
            )

        origins = self.cors_origin_list
        if "*" in origins:
            problems.append(
                "CORS_ORIGINS contains '*'. List only your real frontend origins."
            )
        elif not origins:
            problems.append(
                "CORS_ORIGINS is empty, so the deployed frontend cannot call the "
                "API. Set it to your public frontend origin, e.g. "
                "https://your-app.onrender.com"
            )

        if self.publish_engine == "n8n":
            if not self.internal_service_secret:
                problems.append(
                    "PUBLISH_ENGINE is 'n8n' but INTERNAL_SERVICE_SECRET is "
                    "unset, so /internal/publish returns 404 and the workflow "
                    "cannot publish anything. Set INTERNAL_SERVICE_SECRET, or "
                    "switch PUBLISH_ENGINE to 'direct'."
                )
            if self.is_sqlite is False and self.n8n_backend_base_url.startswith(
                ("http://localhost", "http://127.0.0.1")
            ):
                problems.append(
                    "N8N_BACKEND_BASE_URL is a loopback address ('"
                    f"{self.n8n_backend_base_url}'), which resolves to the n8n "
                    "container itself, not this API. Set it to this service's "
                    "public URL, e.g. https://your-api.onrender.com"
                )

        return problems

    def startup_warnings(self) -> list[str]:
        """Non-fatal production concerns worth surfacing in the log."""
        warnings: list[str] = []

        if self.debug:
            warnings.append("DEBUG is true; set DEBUG=false in production.")

        if self.media_storage_backend == "local":
            warnings.append(
                "MEDIA_STORAGE_BACKEND is 'local'. Render's filesystem is "
                "ephemeral, so uploaded media disappears on every redeploy and "
                "already-scheduled posts will publish without their image. Use "
                "object storage (MEDIA_STORAGE_BACKEND=s3) or a mounted disk."
            )

        media_base = self.media_base_url
        if media_base.startswith(("http://localhost", "http://127.0.0.1")):
            warnings.append(
                f"Media resolves to '{media_base}'. Buffer downloads media from "
                "the public internet and cannot reach a loopback address; posts "
                "will be created without their image and without an error. Set "
                "PUBLIC_MEDIA_BASE_URL to a public URL."
            )

        if not self.n8n_webhook_secret:
            warnings.append(
                "N8N_WEBHOOK_SECRET is unset; the webhook accepts unauthenticated "
                "publish requests."
            )

        return warnings


@lru_cache
def get_settings() -> Settings:
    return Settings()


SettingsDep = Annotated[Settings, Field(default_factory=get_settings)]