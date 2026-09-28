from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import Complaint, ComplaintEvent, WhatsAppMessage

from .test_ingest import make_client, payload


def test_groups_by_author_window_and_quoted_message(tmp_path):
    client, session = make_client(tmp_path)
    headers = {"Authorization": "Bearer test-token"}
    start = datetime(2026, 9, 28, 6, 30, tzinfo=timezone.utc)

    first = client.post(
        "/api/internal/whatsapp/messages",
        data=payload(wa_message_id="first", source_timestamp=start.isoformat()),
        headers=headers,
    )
    close = client.post(
        "/api/internal/whatsapp/messages",
        data=payload(
            wa_message_id="close",
            source_timestamp=(start + timedelta(minutes=9)).isoformat(),
        ),
        headers=headers,
    )
    late = client.post(
        "/api/internal/whatsapp/messages",
        data=payload(
            wa_message_id="late",
            source_timestamp=(start + timedelta(minutes=20)).isoformat(),
        ),
        headers=headers,
    )
    quoted = client.post(
        "/api/internal/whatsapp/messages",
        data=payload(
            wa_message_id="quoted",
            author_id="other-author@lid",
            quoted_message_id="first",
            source_timestamp=(start + timedelta(minutes=30)).isoformat(),
        ),
        headers=headers,
    )

    assert first.json()["complaint_id"] == close.json()["complaint_id"]
    assert late.json()["complaint_id"] != first.json()["complaint_id"]
    assert quoted.json()["complaint_id"] == first.json()["complaint_id"]
    assert quoted.json()["grouping_rule"] == "quoted_message"
    assert len(session.scalars(select(Complaint)).all()) == 2

    draft_list = client.get("/complaints/drafts")
    assert draft_list.status_code == 200
    assert "DRAFT-0001" in draft_list.text
    draft_detail = client.get(f"/drafts/{first.json()['complaint_id']}")
    assert draft_detail.status_code == 200
    assert "panel-test-token" in draft_detail.text


def test_manual_split_and_merge_preserve_messages_and_audit(tmp_path):
    client, session = make_client(tmp_path)
    headers = {"Authorization": "Bearer test-token"}
    start = datetime(2026, 9, 28, 6, 30, tzinfo=timezone.utc)

    for index in range(3):
        response = client.post(
            "/api/internal/whatsapp/messages",
            data=payload(
                wa_message_id=f"message-{index}",
                source_timestamp=(start + timedelta(minutes=index)).isoformat(),
            ),
            headers=headers,
        )
        assert response.status_code == 200

    messages = session.scalars(select(WhatsAppMessage).order_by(WhatsAppMessage.id)).all()
    source_id = messages[0].complaint_id
    split = client.post(
        f"/drafts/{source_id}/split",
        data={"action_token": "panel-test-token", "message_ids": str(messages[-1].id)},
        follow_redirects=False,
    )
    assert split.status_code == 303

    session.expire_all()
    active = session.scalars(select(Complaint).where(Complaint.merged_into_id.is_(None))).all()
    assert len(active) == 2
    target = next(complaint for complaint in active if complaint.id != source_id)
    assert session.get(WhatsAppMessage, messages[-1].id).complaint_id == target.id

    merge = client.post(
        f"/drafts/{target.id}/merge",
        data={"action_token": "panel-test-token", "target_complaint_id": str(source_id)},
        follow_redirects=False,
    )
    assert merge.status_code == 303

    session.expire_all()
    assert session.get(Complaint, target.id).merged_into_id == source_id
    assert all(message.complaint_id == source_id for message in session.scalars(select(WhatsAppMessage)))
    event_types = set(session.scalars(select(ComplaintEvent.event_type)).all())
    assert {"messages_split_out", "messages_split_in", "draft_merged", "draft_merged_into"} <= event_types


def test_panel_actions_require_token(tmp_path):
    client, session = make_client(tmp_path)
    complaint = Complaint(draft_number="DRAFT-0001")
    session.add(complaint)
    session.commit()

    response = client.post(
        f"/drafts/{complaint.id}/merge",
        data={"action_token": "wrong", "target_complaint_id": complaint.id},
    )
    assert response.status_code == 403
