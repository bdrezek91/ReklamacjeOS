from sqlalchemy import select

from app.models import Attachment, ComplaintEvent, OcrResult
from app.ocr_worker import parse_tsv

from .test_ingest import make_client, payload


def test_parse_tsv_preserves_lines_and_calculates_weighted_confidence():
    output = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t0\t0\t1\t1\t80\tPanel\n"
        "5\t1\t1\t1\t1\t2\t0\t0\t1\t1\t90\tZS-3128\n"
        "5\t1\t1\t1\t2\t1\t0\t0\t1\t1\t70\t4 sztuki\n"
    )

    text, confidence = parse_tsv(output)

    assert text == "Panel ZS-3128\n4 sztuki"
    assert confidence is not None
    assert 79 < confidence < 82


def test_queues_and_reviews_local_ocr(tmp_path):
    client, session = make_client(tmp_path)
    response = client.post(
        "/api/internal/whatsapp/messages",
        data=payload(wa_message_id="ocr-photo"),
        files={"media": ("etykieta.jpg", b"fake-image", "image/jpeg")},
        headers={"Authorization": "Bearer test-token"},
    )
    complaint_id = response.json()["complaint_id"]
    attachment = session.scalar(select(Attachment))

    queued = client.post(
        f"/drafts/{complaint_id}/ocr",
        data={"action_token": "panel-test-token"},
        follow_redirects=False,
    )
    assert queued.status_code == 303
    result = session.scalar(select(OcrResult))
    assert result.attachment_id == attachment.id
    assert result.status == "pending"

    result.status = "completed"
    result.raw_text = "Panel 2S-312B"
    result.confidence = 71.0
    session.commit()
    corrected = client.post(
        f"/drafts/{complaint_id}/ocr/{attachment.id}",
        data={"action_token": "panel-test-token", "corrected_text": "Panel ZS-3128"},
        follow_redirects=False,
    )
    assert corrected.status_code == 303
    session.refresh(result)
    assert result.corrected_text == "Panel ZS-3128"
    assert result.reviewed_at is not None
    event_types = set(session.scalars(select(ComplaintEvent.event_type)).all())
    assert {"ocr_queued", "ocr_text_reviewed"} <= event_types
