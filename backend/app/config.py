import os
from dataclasses import dataclass
from pathlib import Path


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def read_secret(name: str, file_name: str) -> str:
    path = os.getenv(file_name, "").strip()
    if path:
        try:
            return Path(path).read_text().strip()
        except OSError:
            return ""
    return os.getenv(name, "")


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
    panel_user: str = os.getenv("PANEL_USER", "").strip()
    panel_password_hash_b64: str = os.getenv("PANEL_PASSWORD_HASH_B64", "").strip()
    auth_session_secret: str = os.getenv("AUTH_SESSION_SECRET", "").strip()
    grouping_window_minutes: int = int(os.getenv("GROUPING_WINDOW_MINUTES", "10"))
    data_root: Path = Path(os.getenv("DATA_ROOT", "/data/reklamacje"))
    max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_BYTES", str(15 * 1024 * 1024)))
    smtp_host: str = os.getenv("SMTP_HOST", "").strip()
    smtp_enabled: bool = env_bool("SMTP_ENABLED", False)
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_username: str = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password: str = read_secret("SMTP_PASSWORD", "SMTP_PASSWORD_FILE")
    smtp_from_address: str = os.getenv("SMTP_FROM_ADDRESS", "").strip()
    smtp_from_name: str = os.getenv("SMTP_FROM_NAME", "Reklamacje DAMPOL").strip()
    smtp_starttls: bool = env_bool("SMTP_STARTTLS", True)
    smtp_ssl: bool = env_bool("SMTP_SSL", False)
    smtp_timeout_seconds: int = int(os.getenv("SMTP_TIMEOUT_SECONDS", "30"))
    email_worker_interval_seconds: int = int(os.getenv("EMAIL_WORKER_INTERVAL_SECONDS", "5"))
    typesafe_enabled: bool = env_bool("TYPESAFE_ENABLED", False)
    typesafe_api_key: str = read_secret("TYPESAFE_API_KEY", "TYPESAFE_API_KEY_FILE")
    typesafe_model: str = os.getenv("TYPESAFE_MODEL", "jev-latest").strip()
    typesafe_timeout_seconds: int = int(os.getenv("TYPESAFE_TIMEOUT_SECONDS", "15"))
    ocr_language: str = os.getenv("OCR_LANGUAGE", "pol+eng").strip()
    ocr_timeout_seconds: int = int(os.getenv("OCR_TIMEOUT_SECONDS", "90"))
    ocr_worker_interval_seconds: int = int(os.getenv("OCR_WORKER_INTERVAL_SECONDS", "3"))

    @property
    def smtp_configured(self) -> bool:
        has_credentials = not self.smtp_username or bool(self.smtp_password)
        return bool(self.smtp_enabled and self.smtp_host and self.smtp_from_address and has_credentials)

    @property
    def typesafe_configured(self) -> bool:
        return bool(self.typesafe_enabled and self.typesafe_api_key and self.typesafe_model)


settings = Settings()
