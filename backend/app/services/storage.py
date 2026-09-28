import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from ..config import settings

ALLOWED_MIME_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
}


@dataclass(frozen=True)
class StoredFile:
    relative_path: str
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256: str


def safe_part(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return sanitized[:160] or "unknown"


async def store_original_media(
    upload: UploadFile,
    message_id: str,
    source_timestamp: datetime,
) -> StoredFile:
    mime_type = (upload.content_type or "").lower()
    if mime_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported media type: {mime_type or 'unknown'}",
        )

    content = await upload.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Attachment too large")
    if not content:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Empty attachment")

    digest = hashlib.sha256(content).hexdigest()
    extension = ALLOWED_MIME_TYPES[mime_type]
    filename = safe_part(upload.filename or f"media{extension}")
    if Path(filename).suffix.lower() not in {extension, ".jpeg" if extension == ".jpg" else extension}:
        filename = f"{filename}{extension}"

    relative_dir = Path(str(source_timestamp.year)) / "INBOX" / "whatsapp" / safe_part(message_id) / "original"
    relative_path = relative_dir / f"{digest[:12]}_{filename}"
    absolute_path = settings.data_root / relative_path
    absolute_path.parent.mkdir(parents=True, exist_ok=True)

    if not absolute_path.exists():
        temporary_path = absolute_path.with_suffix(absolute_path.suffix + ".tmp")
        temporary_path.write_bytes(content)
        temporary_path.replace(absolute_path)

    return StoredFile(
        relative_path=relative_path.as_posix(),
        original_filename=filename,
        mime_type=mime_type,
        size_bytes=len(content),
        sha256=digest,
    )
