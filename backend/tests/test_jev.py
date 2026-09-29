from sqlalchemy import select

from app.models import Attachment, Complaint, ComplaintEvent, ComplaintStatus, OcrResult
from app.services.jev import analyze_complaint_with_jev

from .test_ingest import make_client, payload


class FakeResponse:
    def model_dump(self, *, mode: str):
        assert mode == "json"
        return {
            "model": "jev-test",
            "answers": {
                "is_complaint": {"type": "noul", "noul": 0.97},
                "urgency": {
                    "type": "score",
                    "score": 1.25,
                    "confidence": 0.8,
                    "legend": {"0": "low", "1": "medium", "2": "high"},
                    "probabilities": {"0": 0.1, "1": 0.55, "2": 0.35},
                },
            },
            "usage": {"input_tokens": 123, "output_tokens": 4},
        }


class FakeClient:
    def __init__(self):
        self.state = None
        self.questions = None

    def system_one(self, state, questions, *, model):
        assert model == "jev-latest"
        self.state = state
        self.questions = questions
        return FakeResponse()


def test_jev_analysis_records_typed_results_without_changing_complaint(tmp_path):
    client, session = make_client(tmp_path)
    response = client.post(
        "/api/internal/whatsapp/messages",
        data=payload(body="Płyta jest uszkodzona, ilość 4 sztuki"),
        files={"media": ("etykieta.jpg", b"fake-image", "image/jpeg")},
        headers={"Authorization": "Bearer test-token"},
    )
    complaint_id = response.json()["complaint_id"]
    complaint = session.get(Complaint, complaint_id)
    original_status = complaint.status
    attachment = session.scalar(select(Attachment))
    session.add(
        OcrResult(
            attachment_id=attachment.id,
            status="completed",
            raw_text="niepoprawny odczyt",
            corrected_text="Panel ZS-3128, 4 sztuki",
            confidence=88.5,
        )
    )
    session.commit()
    fake_client = FakeClient()

    assessment = analyze_complaint_with_jev(session, complaint_id, client=fake_client)
    session.commit()

    assert assessment.status == "completed"
    assert assessment.model == "jev-test"
    assert assessment.answers["is_complaint"]["noul"] == 0.97
    assert assessment.usage["input_tokens"] == 123
    assert len(assessment.input_hash) == 64
    assert session.get(Complaint, complaint_id).status == original_status == ComplaintStatus.DRAFT
    assert fake_client.state["messages"][0]["body"] == "Płyta jest uszkodzona, ilość 4 sztuki"
    assert "group_id" not in fake_client.state["messages"][0]
    assert "author_id" not in fake_client.state["messages"][0]
    assert fake_client.state["ocr"] == [
        {
            "attachment_id": attachment.id,
            "text": "Panel ZS-3128, 4 sztuki",
            "confidence": 88.5,
            "reviewed": False,
        }
    ]
    assert "needs_human_review" in fake_client.questions
    assert any(event.event_type == "jev_analysis_completed" for event in session.query(ComplaintEvent).all())


def test_jev_endpoint_is_unavailable_while_integration_is_disabled(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.post(
        "/drafts/1/analyze-jev",
        data={"action_token": "panel-test-token"},
    )
    assert response.status_code == 503
