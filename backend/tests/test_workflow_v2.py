from sqlalchemy import select

from app.models import Complaint, ComplaintStatus, EmailDraft, Supplier, WhatsAppOutbox
from app.services.intake import extract_fields_from_text

from .test_ingest import make_client, payload


def test_intake_extracts_panel_data_from_message():
    extracted = extract_fields_from_text(
        "Uszkodzone zamki. 12 sztuk, wymiar 3000x1000 mm, zamówienie ZS-2875, paczka PK-77."
    )

    assert extracted["quantity"] == "12 szt."
    assert extracted["dimensions"] == "3000 x 1000"
    assert extracted["paneltech_order_number"] == "ZS-2875"
    assert extracted["package_number"] == "PK-77"
    assert "Uszkodzone zamki" in extracted["defect_description"]


def test_missing_data_reply_completes_same_complaint_and_prepares_email(tmp_path):
    client, session = make_client(tmp_path)
    session.add(
        Supplier(
            name="Paneltech",
            email="reklamacje@paneltech.pl",
            active=True,
        )
    )
    session.commit()

    headers = {"Authorization": "Bearer test-token"}
    incoming = client.post(
        "/api/internal/whatsapp/messages",
        data=payload(
            wa_message_id="initial-damage",
            body="Uszkodzone zamki płyty dachowej.",
        ),
        headers=headers,
    )
    assert incoming.status_code == 200
    complaint_id = incoming.json()["complaint_id"]
    detail = client.get(f"/drafts/{complaint_id}")
    assert detail.status_code == 200
    assert "Wykorzystać jako I gatunek w innym pawilonie" in detail.text
    assert "Zwrot do Paneltech" in detail.text
    assert "Zostawić płyty i negocjować rabat" in detail.text
    assert "II gatunek – rabat 50%" in detail.text

    decision = client.post(
        f"/drafts/{complaint_id}/decision",
        data={
            "action_token": "panel-test-token",
            "strategy": "keep_request_discount",
            "discount_percent": "30",
        },
        follow_redirects=False,
    )
    assert decision.status_code == 303

    session.expire_all()
    complaint = session.get(Complaint, complaint_id)
    assert complaint.status == ComplaintStatus.DRAFT
    assert complaint.resolution_strategy == "keep_request_discount"
    assert complaint.resolution_discount_percent == 30

    missing_question = session.scalar(
        select(WhatsAppOutbox).where(
            WhatsAppOutbox.complaint_id == complaint_id,
            WhatsAppOutbox.message_kind == "missing_data",
        )
    )
    assert missing_question is not None
    assert "ilość sztuk" in missing_question.body
    assert "wymiary płyt" in missing_question.body
    assert "nr zamówienia Paneltech lub nr paczki" in missing_question.body
    claimed = client.post(
        "/api/internal/whatsapp/outbox/claim",
        headers=headers,
    )
    assert claimed.status_code == 200
    assert claimed.json()["id"] == missing_question.id

    sent = client.post(
        f"/api/internal/whatsapp/outbox/{missing_question.id}/sent",
        data={"wa_message_id": "system-missing-question"},
        headers=headers,
    )
    assert sent.status_code == 200

    reply = client.post(
        "/api/internal/whatsapp/messages",
        data=payload(
            wa_message_id="missing-data-reply",
            author_id="different-worker@lid",
            author_name="Inny pracownik",
            quoted_message_id="system-missing-question",
            body="12 sztuk, 3000x1000 mm, zamówienie ZS-2875.",
        ),
        headers=headers,
    )
    assert reply.status_code == 200
    assert reply.json()["complaint_id"] == complaint_id
    assert reply.json()["grouping_rule"] == "quoted_system_message"

    session.expire_all()
    complaint = session.get(Complaint, complaint_id)
    assert complaint.approved_data["quantity"] == "12 szt."
    assert complaint.approved_data["dimensions"] == "3000 x 1000"
    assert complaint.approved_data["paneltech_order_number"] == "ZS-2875"
    assert "Uszkodzone zamki" in complaint.approved_data["defect_description"]
    pending = client.post(
        f"/drafts/{complaint_id}/status",
        data={"action_token": "panel-test-token", "target_status": "pending_approval"},
        follow_redirects=False,
    )
    assert pending.status_code == 303

    accepted = client.post(
        f"/drafts/{complaint_id}/accept",
        data={"action_token": "panel-test-token"},
        follow_redirects=False,
    )
    assert accepted.status_code == 303

    session.expire_all()
    complaint = session.get(Complaint, complaint_id)
    draft = session.scalar(select(EmailDraft).where(EmailDraft.complaint_id == complaint_id))
    assert complaint.status == ComplaintStatus.READY_TO_SEND
    assert complaint.supplier is not None
    assert complaint.supplier.name == "Paneltech"
    assert draft is not None
    assert "ZS-2875" in draft.body
    assert "30%" in draft.body

    kinds = set(
        session.scalars(
            select(WhatsAppOutbox.message_kind).where(WhatsAppOutbox.complaint_id == complaint_id)
        ).all()
    )
    assert {"missing_data", "acceptance"} <= kinds
