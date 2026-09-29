import csv
import hashlib
import io
import json
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from .config import settings
from .db import SessionLocal
from .models import Attachment, ComplaintEvent, OcrResult, WhatsAppMessage
from .services.ocr import MAX_OCR_TEXT_LENGTH

RUNNING = True
READY_FILE = Path("/tmp/ocr-worker-ready")


def log(message: str, **metadata: object) -> None:
    print(json.dumps({"time": datetime.now().astimezone().isoformat(), "message": message, **metadata}), flush=True)


def claim_next() -> int | None:
    with SessionLocal.begin() as db:
        query = select(OcrResult).where(OcrResult.status == "pending").order_by(OcrResult.id)
        if db.get_bind().dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        result = db.scalar(query.limit(1))
        if result is None:
            return None
        result.status = "processing"
        result.attempt_count += 1
        result.claimed_at = datetime.now().astimezone()
        return result.id


def attachment_path(attachment: Attachment) -> Path:
    path = (settings.data_root / attachment.storage_path).resolve()
    root = settings.data_root.resolve()
    if root not in path.parents or not path.is_file():
        raise FileNotFoundError("Nie znaleziono pliku źródłowego")
    if hashlib.sha256(path.read_bytes()).hexdigest() != attachment.sha256:
        raise ValueError("Suma kontrolna zdjęcia jest niezgodna")
    return path


def parse_tsv(output: str) -> tuple[str, float | None]:
    lines: dict[tuple[str, str, str, str], list[str]] = {}
    confidences: list[tuple[float, int]] = []
    for row in csv.DictReader(io.StringIO(output), delimiter="\t"):
        word = (row.get("text") or "").strip()
        if not word:
            continue
        key = tuple(row.get(name, "") for name in ("page_num", "block_num", "par_num", "line_num"))
        lines.setdefault(key, []).append(word)
        try:
            confidence = float(row.get("conf", "-1"))
        except ValueError:
            confidence = -1
        if confidence >= 0:
            confidences.append((confidence, len(word)))
    text = "\n".join(" ".join(words) for words in lines.values())[:MAX_OCR_TEXT_LENGTH]
    total_weight = sum(weight for _, weight in confidences)
    confidence = sum(value * weight for value, weight in confidences) / total_weight if total_weight else None
    return text, confidence


def recognize(path: Path) -> tuple[str, float | None, str]:
    version = subprocess.run(
        ["tesseract", "--version"], capture_output=True, text=True, timeout=10, check=True
    ).stdout.splitlines()[0][:64]
    completed = subprocess.run(
        ["tesseract", str(path), "stdout", "-l", settings.ocr_language, "--psm", "11", "tsv"],
        capture_output=True,
        text=True,
        timeout=settings.ocr_timeout_seconds,
        check=True,
    )
    text, confidence = parse_tsv(completed.stdout)
    return text, confidence, version


def process_one(result_id: int) -> None:
    with SessionLocal.begin() as db:
        result = db.get(OcrResult, result_id)
        if result is None or result.status != "processing":
            return
        attachment = db.get(Attachment, result.attachment_id)
        if attachment is None:
            return
        message = db.get(WhatsAppMessage, attachment.whatsapp_message_id)
        try:
            text, confidence, version = recognize(attachment_path(attachment))
            result.raw_text = text
            result.confidence = confidence
            result.engine_version = version
            result.status = "completed"
            result.completed_at = datetime.now().astimezone()
            result.last_error = None
            if message and message.complaint_id:
                db.add(
                    ComplaintEvent(
                        complaint_id=message.complaint_id,
                        event_type="ocr_completed",
                        actor="ocr-worker",
                        details={"attachment_id": attachment.id, "text_length": len(text)},
                    )
                )
        except Exception as error:
            result.status = "failed"
            result.last_error = f"{type(error).__name__}: OCR nie powiódł się"
            result.completed_at = datetime.now().astimezone()
            if message and message.complaint_id:
                db.add(
                    ComplaintEvent(
                        complaint_id=message.complaint_id,
                        event_type="ocr_failed",
                        actor="ocr-worker",
                        details={"attachment_id": attachment.id, "error_type": type(error).__name__},
                    )
                )


def stop_worker(*_: object) -> None:
    global RUNNING
    RUNNING = False


def recover_interrupted_jobs() -> int:
    with SessionLocal.begin() as db:
        results = db.scalars(select(OcrResult).where(OcrResult.status == "processing")).all()
        for result in results:
            result.status = "pending"
            result.claimed_at = None
        return len(results)


def main() -> None:
    signal.signal(signal.SIGTERM, stop_worker)
    signal.signal(signal.SIGINT, stop_worker)
    recovered = recover_interrupted_jobs()
    READY_FILE.write_text("ready\n")
    log("Worker OCR gotowy", engine="tesseract", language=settings.ocr_language, recovered=recovered)
    while RUNNING:
        result_id = claim_next()
        if result_id is None:
            time.sleep(settings.ocr_worker_interval_seconds)
            continue
        process_one(result_id)


if __name__ == "__main__":
    main()
