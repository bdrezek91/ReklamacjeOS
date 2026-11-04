import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Complaint, ComplaintStatus, EmailOutbox, Supplier, SupplierReplyOutbox, WhatsAppMessage
from app.email_worker import build_message
from app.services.grouping import DraftOperationError
from app.services.supplier_acceptance import (
    default_acceptance_body,
    enqueue_supplier_acceptance,
    offered_discount,
)
from app.services.supplier_thread import find_complaint_for_email, latest_supplier_email


class SupplierAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.db.add(Supplier(id=1,name="Paneltech",email="reklamacje@paneltech.pl",active=True))
        self.db.add(Complaint(
            id=10,draft_number="DRAFT-0010",official_number="R/01/11/2026",
            supplier_id=1,status=ComplaintStatus.SENT,
            approved_data={"quantity":"3 szt.","material_product":"Płyta dachowa","paneltech_order_number":"ZS-4720"},
            resolution_strategy="second_grade_50",
        ))
        self.incoming_id="<incoming-test@paneltech.pl>"
        self.initial_id="<initial-test@dampol-investment.com>"
        self.db.add(WhatsAppMessage(
            complaint_id=10,wa_message_id="email:INBOX:8676",group_id="email-thread",
            body="Proponujemy 40% rabatu na 3 sztuki. Prosimy o akceptację.",
            message_type="email",source_timestamp=datetime(2026,11,4,11,49,tzinfo=timezone.utc),
            source_payload={
                "source":"email","direction":"incoming","sender":"<reklamacje@paneltech.pl>",
                "subject":"RE: Reklamacja R/01/11/2026","message_id":self.incoming_id,
                "in_reply_to":self.initial_id,"references":self.initial_id,
            },
        ))
        self.db.commit()
        self.cfg=SimpleNamespace(smtp_configured=True,smtp_from_address="bartlomiej.drezek@dampol-investment.com")

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_explicit_acceptance_is_idempotent_and_threaded(self):
        from app.services import supplier_acceptance
        incoming=latest_supplier_email(self.db.get(Complaint,10))
        body=default_acceptance_body(self.db.get(Complaint,10),incoming,40)
        self.assertIn("40%",body)
        self.assertIn("ZS-4720",body)
        with patch.object(supplier_acceptance,"settings",self.cfg):
            item=enqueue_supplier_acceptance(self.db,10,
                reply_to_source_id="email:INBOX:8676",discount_percent=40,body=body,actor="test")
            msg=build_message(item)
            self.assertEqual(msg["In-Reply-To"],self.incoming_id)
            self.assertIn(self.initial_id,msg["References"])
            self.assertEqual(msg["To"],"reklamacje@paneltech.pl")
            self.assertIn("40%",msg.get_body(preferencelist=("plain",)).get_content())
            self.assertEqual(item.status,"pending")
            with self.assertRaisesRegex(DraftOperationError,"już zostało zlecone"):
                enqueue_supplier_acceptance(self.db,10,
                    reply_to_source_id="email:INBOX:8676",discount_percent=40,body=body,actor="test")
            self.db.rollback()

    def test_wrong_percent_or_stale_reply_never_queues(self):
        from app.services import supplier_acceptance
        with patch.object(supplier_acceptance,"settings",self.cfg):
            for percent,source in [(50,"email:INBOX:8676"),(40,"email:INBOX:9999")]:
                with self.assertRaises(DraftOperationError):
                    enqueue_supplier_acceptance(
                        self.db,10,reply_to_source_id=source,discount_percent=percent,
                        body="Potwierdzamy rabat 40%.",actor="test")
                self.db.rollback()
            self.assertIsNone(self.db.scalar(select(SupplierReplyOutbox)))

    def test_match_native_smtp_message_id_before_sent_copy(self):
        self.db.add(EmailOutbox(
            complaint_id=10,sender="bartlomiej.drezek@dampol-investment.com",
            recipient="reklamacje@paneltech.pl",subject="Reklamacja R/01/11/2026",
            body="Opis wady",attachments=[],message_id=self.initial_id,status="sent",
        ))
        self.db.commit()
        complaint,rule=find_complaint_for_email(
            self.db,in_reply_to=self.initial_id,references="",
            subject="RE: Reklamacja R/01/11/2026",
            body="Proponujemy 40% rabatu.",
            source_timestamp=datetime.now(timezone.utc),sender="reklamacje@paneltech.pl",
        )
        self.assertEqual(complaint.id,10)
        self.assertEqual(rule,"native_smtp_message_id")

    def test_offer_percent_requires_explicit_discount_word(self):
        self.assertEqual(offered_discount("proponujemy 40% rabatu"),40)
        self.assertIsNone(offered_discount("50% długości i 40 mm grubości"))
        self.assertIsNone(offered_discount("proponujemy 180% rabatu"))


if __name__=="__main__":
    unittest.main()
