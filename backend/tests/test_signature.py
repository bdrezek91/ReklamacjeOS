import unittest
from email.message import EmailMessage

from app.services.signature import compose_body, set_message_content


class SignatureTests(unittest.TestCase):
    def test_signature_matches_company_style_and_is_idempotent(self):
        body = compose_body("Dzień dobry,\n\nZgłaszamy reklamację.\n\nPozdrawiamy")
        self.assertEqual(body, compose_body(body))
        self.assertIn("Bartłomiej Drężek", body)
        self.assertIn("Dampol Investment", body)
        self.assertIn("INFORMACJA RODO", body)
        self.assertNotIn("Pozdrawiamy\n\n--", body)

    def test_html_signature_embeds_banner_once(self):
        msg = EmailMessage()
        set_message_content(msg, compose_body("Dzień dobry."))
        types = [part.get_content_type() for part in msg.walk()]
        self.assertIn("text/plain", types)
        self.assertIn("text/html", types)
        self.assertEqual(types.count("image/jpeg"), 1)
        banner = next(p for p in msg.walk() if p.get_content_type() == "image/jpeg")
        self.assertEqual(banner["Content-ID"], "<dampol-signature>")

    def test_historical_message_is_not_rewritten(self):
        msg = EmailMessage()
        set_message_content(msg, "Dzień dobry.\n\nPozdrawiamy")
        self.assertFalse(msg.is_multipart())
        self.assertEqual(msg.get_content(), "Dzień dobry.\n\nPozdrawiamy\n")


if __name__ == "__main__":
    unittest.main()
