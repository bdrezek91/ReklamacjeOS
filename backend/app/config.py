import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://reklamacje:reklamacje@postgres:5432/reklamacje",
    )
    bridge_api_token: str = os.getenv("BRIDGE_API_TOKEN", "")
    whatsapp_group_id: str = os.getenv("WHATSAPP_GROUP_ID", "")
    root_path: str = os.getenv("ROOT_PATH", "").rstrip("/")
    data_root: Path = Path(os.getenv("DATA_ROOT", "/data/reklamacje"))
    max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_BYTES", str(15 * 1024 * 1024)))


settings = Settings()
