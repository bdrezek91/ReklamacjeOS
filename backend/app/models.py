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
    ACCEPTED = "accepted"
    READY_TO_SEND = "ready_to_send"
    SENT = "sent"
    CLOSED = "closed"


class ComplaintNumberCounter(Base):
    __tablename__ = "complaint_number_counters"

    year: Mapped[int] = mapped_column(primary_key=True)
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ComplaintMonthlyNumberCounter(Base):
    __tablename__ = "complaint_monthly_number_counters"

    year: Mapped[int] = mapped_column(primary_key=True)
    month: Mapped[int] = mapped_column(primary_key=True)
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Complaint(Base):
    __tablename__ = "complaints"

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    official_number: Mapped[str | None] = mapped_column(String(32), unique=True)
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("complaints.id", ondelete="SET NULL"), index=True)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id", ondelete="SET NULL"), index=True)
    status: Mapped[ComplaintStatus] = mapped_column(Enum(ComplaintStatus), default=ComplaintStatus.DRAFT)
    approved_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list[WhatsAppMessage]] = relationship(back_populates="complaint")
    events: Mapped[list[ComplaintEvent]] = relationship(back_populates="complaint", cascade="all, delete-orphan")
    merged_into: Mapped[Complaint | None] = relationship(remote_side="Complaint.id", foreign_keys=[merged_into_id])
    supplier: Mapped[Supplier | None] = relationship(back_populates="complaints")
    jev_assessments: Mapped[list[JevAssessment]] = relationship(
        back_populates="complaint", cascade="all, delete-orphan"
    )


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    contact_person: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    complaints: Mapped[list[Complaint]] = relationship(back_populates="supplier")


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


class JevAssessment(Base):
    __tablename__ = "jev_assessments"

    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="typesafe")
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    answers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    complaint: Mapped[Complaint] = relationship(back_populates="jev_assessments")


class WhatsAppOutbox(Base):
    __tablename__ = "whatsapp_outbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id", ondelete="CASCADE"), unique=True, index=True)
    group_id: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wa_message_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailDraft(Base):
    __tablename__ = "email_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id", ondelete="CASCADE"), unique=True, index=True)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(998), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EmailOutbox(Base):
    __tablename__ = "email_outbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    complaint_id: Mapped[int] = mapped_column(ForeignKey("complaints.id", ondelete="CASCADE"), unique=True, index=True)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    sender: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(998), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    message_id: Mapped[str] = mapped_column(String(998), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
