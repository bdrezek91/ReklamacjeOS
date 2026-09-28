from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class ComplaintStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    SENT = "sent"
    CLOSED = "closed"


class Complaint(Base):
    __tablename__ = "complaints"

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    official_number: Mapped[str | None] = mapped_column(String(32), unique=True)
    merged_into_id: Mapped[int | None] = mapped_column(
        ForeignKey("complaints.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[ComplaintStatus] = mapped_column(Enum(ComplaintStatus), default=ComplaintStatus.DRAFT)
    approved_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list[WhatsAppMessage]] = relationship(back_populates="complaint")
    events: Mapped[list[ComplaintEvent]] = relationship(
        back_populates="complaint", cascade="all, delete-orphan"
    )
    merged_into: Mapped[Complaint | None] = relationship(
        remote_side="Complaint.id", foreign_keys=[merged_into_id]
    )


class WhatsAppMessage(Base):
    __tablename__ = "whatsapp_messages"
    __table_args__ = (UniqueConstraint("wa_message_id", name="uq_whatsapp_messages_wa_message_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_id: Mapped[int | None] = mapped_column(ForeignKey("complaints.id", ondelete="SET NULL"), index=True)
    wa_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    group_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    group_name: Mapped[str | None] = mapped_column(String(255))
    author_id: Mapped[str | None] = mapped_column(String(255))
    author_name: Mapped[str | None] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    message_type: Mapped[str] = mapped_column(String(64), nullable=False)
    quoted_message_id: Mapped[str | None] = mapped_column(String(255), index=True)
    source_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    attachments: Mapped[list["Attachment"]] = relationship(back_populates="message", cascade="all, delete-orphan")
    complaint: Mapped[Complaint | None] = relationship(back_populates="messages")


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    whatsapp_message_id: Mapped[int] = mapped_column(ForeignKey("whatsapp_messages.id", ondelete="CASCADE"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    include_in_email: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    message: Mapped[WhatsAppMessage] = relationship(back_populates="attachments")


class ComplaintEvent(Base):
    __tablename__ = "complaint_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    complaint: Mapped[Complaint] = relationship(back_populates="events")


class ComplaintField(Base):
    __tablename__ = "complaint_fields"

    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id", ondelete="CASCADE"), index=True)
    field_name: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    confidence: Mapped[int | None] = mapped_column(Integer)
    is_uncertain: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmailDraft(Base):
    __tablename__ = "email_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id", ondelete="CASCADE"), index=True)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(998), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EmailSent(Base):
    __tablename__ = "email_sent"

    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id", ondelete="RESTRICT"), index=True)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(998), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    message_id: Mapped[str | None] = mapped_column(String(998))
    smtp_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
