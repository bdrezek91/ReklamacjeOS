import os
from dataclasses import dataclass
from pathlib import Path


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://reklamacje:reklamacje@postgres:5432/reklamacje",
    )
    bridge_api_token: str = os.getenv("BRIDGE_API_TOKEN", "")
    whatsapp_group_id: str = os.getenv("WHATSAPP_GROUP_ID", "")
    root_path: str = os.getenv("ROOT_PATH", "").rstrip("/")
    panel_action_token: str = os.getenv("PANEL_ACTION_TOKEN", "")
    grouping_window_minutes: int = int(os.getenv("GROUPING_WINDOW_MINUTES", "10"))
    data_root: Path = Path(os.getenv("DATA_ROOT", "/data/reklamacje"))
    max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_BYTES", str(15 * 1024 * 1024)))
    smtp_host: str = os.getenv("SMTP_HOST", "").strip()
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_username: str = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_from_address: str = os.getenv("SMTP_FROM_ADDRESS", "").strip()
    smtp_from_name: str = os.getenv("SMTP_FROM_NAME", "Reklamacje DAMPOL").strip()
    smtp_starttls: bool = env_bool("SMTP_STARTTLS", True)
    smtp_ssl: bool = env_bool("SMTP_SSL", False)
    smtp_timeout_seconds: int = int(os.getenv("SMTP_TIMEOUT_SECONDS", "30"))
    email_worker_interval_seconds: int = int(os.getenv("EMAIL_WORKER_INTERVAL_SECONDS", "5"))

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from_address)


settings = Settings()
