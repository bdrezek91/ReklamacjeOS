from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import WhatsAppMessage


def make_client(tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)

    object.__setattr__(settings, "bridge_api_token", "test-token")
    object.__setattr__(settings, "whatsapp_group_id", "allowed-group@g.us")
    object.__setattr__(settings, "data_root", tmp_path)
    object.__setattr__(settings, "panel_action_token", "panel-test-token")
    object.__setattr__(settings, "grouping_window_minutes", 10)

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app), session


def payload(**overrides):
    data = {
        "wa_message_id": "wamid-1",
        "group_id": "allowed-group@g.us",
        "group_name": "Reklamacje",
        "author_id": "48123123@c.us",
        "author_name": "Marek",
        "body": "ZS-3128 dachowe, wywalone zamki",
        "message_type": "chat",
        "source_timestamp": datetime(2026, 9, 28, 6, 30, tzinfo=timezone.utc).isoformat(),
        "source_payload": "{}",
    }
    data.update(overrides)
    return data


def test_rejects_non_whitelisted_group(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.post(
        "/api/internal/whatsapp/messages",
        data=payload(group_id="other-group@g.us"),
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 403


def test_ingest_is_idempotent(tmp_path):
    client, session = make_client(tmp_path)
    headers = {"Authorization": "Bearer test-token"}

    first = client.post("/api/internal/whatsapp/messages", data=payload(), headers=headers)
    second = client.post("/api/internal/whatsapp/messages", data=payload(), headers=headers)

    assert first.status_code == 200
    assert first.json()["created"] is True
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert len(session.scalars(select(WhatsAppMessage)).all()) == 1


def test_saves_original_image(tmp_path):
    client, session = make_client(tmp_path)
    response = client.post(
        "/api/internal/whatsapp/messages",
        data=payload(wa_message_id="wamid-photo"),
        files={"media": ("wada.jpg", b"fake-jpeg-content", "image/jpeg")},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    message = session.scalar(select(WhatsAppMessage).where(WhatsAppMessage.wa_message_id == "wamid-photo"))
    session.refresh(message)
    assert len(message.attachments) == 1
    stored = tmp_path / message.attachments[0].storage_path
    assert stored.read_bytes() == b"fake-jpeg-content"
