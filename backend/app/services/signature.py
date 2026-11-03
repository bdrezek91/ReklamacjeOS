"""Firmowa stopka poczty, zgodna z PłytyOS (tekst i obraz)."""
import html
from pathlib import Path

def _signature_html():
    legal_pl = (
        "Uwaga: Niniejsza wiadomość, w szczególności jej treść oraz załączniki, może być poufna. "
        "W przypadku, gdy nie jest Pan/Pani jej adresatem informuję, że wszelkie rozpowszechnianie, "
        "dystrybucja lub powielanie powyższej wiadomości jest zabronione. Jednocześnie proszę o "
        "powiadomienie nadawcy oraz niezwłoczne usunięcie powyższej wiadomości wraz z załącznikami. "
        "Ponadto jako administrator danych pouczam o treści art. 13 Rozporządzenia Parlamentu Europejskiego "
        "i Rady (UE) 2016/679 z 27 kwietnia 2016 r. o ochronie danych osobowych (RODO) i podaję, iż Państwa "
        "dane osobowe przetwarzane są w związku ze złożonym zapytaniem ofertowym / ofertą oraz celem zawarcia "
        "umowy sprzedaży lub najmu przedmiotów oferowanych przez Administratora."
    )
    legal_en = (
        "Confidentiality Notice: This email, particularly its content and any attached files, may be confidential. "
        "If you are not an intended recipient, any disclosure, distribution and reproduction of this message is "
        "prohibited. In this case please notify the sender immediately and then delete this message and any attachments."
    )
    return f"""
<div style="background:#ffffff;color:#202124">
  <p style="margin:0;background:#ffffff;font-family:Arial,sans-serif;font-size:11pt;font-style:italic;line-height:normal">--</p>
  <p style="margin:0;background:#ffffff;font-family:Arial,sans-serif;font-size:11pt;font-style:italic;line-height:normal">Z wyrazami szacunku</p>
  <p style="margin:0;background:#ffffff;font-family:Arial,sans-serif;font-size:11pt;line-height:normal">&nbsp;</p>
  <p style="margin:0;background:#ffffff;line-height:normal">
    <img src="cid:dampol-signature" alt="Bartłomiej Drężek - Dampol Investment" width="965" height="235" style="display:block;width:965px;height:235px;border:0">
  </p>
  <p style="margin:0;background:#ffffff;font-family:'Arial Black',Arial,sans-serif;font-size:10pt;font-weight:bold;font-style:italic;color:#AE932B;line-height:normal">
    Dampol Investment Sp. z o.o.&nbsp;&nbsp;&nbsp; NIP: PL 645 258 1961&nbsp;&nbsp;&nbsp; REGON: 525991406&nbsp;&nbsp;&nbsp; KRS: 0001048451
  </p>
  <p style="margin:0;background:#ffffff;font-family:'Arial Narrow',Arial,sans-serif;font-size:10pt;font-weight:bold;font-style:italic;color:#000000;line-height:normal">INFORMACJA RODO</p>
  <p style="margin:0;background:#ffffff;font-family:'Arial Narrow',Arial,sans-serif;font-size:7.5pt;color:#222222;line-height:normal">{html.escape(legal_pl)}</p>
  <p style="margin:0;background:#ffffff;font-family:'Arial Narrow',Arial,sans-serif;font-size:7.5pt;color:#222222;line-height:normal">{html.escape(legal_en)}</p>
</div>
"""

def _signature_plain():
    return """--
Z wyrazami szacunku

Bartłomiej Drężek
Koordynator magazynu
Dampol Investment Sp. z o.o.
bartlomiej.drezek@dampol-investment.com
www.dampol-investment.com
www.dampol-okna.com
ul. Gliwicka 20a, 42-677 Czekanów

Dampol Investment Sp. z o.o.    NIP: PL 645 258 1961    REGON: 525991406    KRS: 0001048451
INFORMACJA RODO
Uwaga: Niniejsza wiadomość, w szczególności jej treść oraz załączniki, może być poufna. W przypadku, gdy nie jest Pan/Pani jej adresatem informuję, że wszelkie rozpowszechnianie, dystrybucja lub powielanie powyższej wiadomości jest zabronione. Jednocześnie proszę o powiadomienie nadawcy oraz niezwłoczne usunięcie powyższej wiadomości wraz z załącznikami. Ponadto jako administrator danych pouczam o treści art. 13 Rozporządzenia Parlamentu Europejskiego i Rady (UE) 2016/679 z 27 kwietnia 2016 r. o ochronie danych osobowych (RODO) i podaję, iż Państwa dane osobowe przetwarzane są w związku ze złożonym zapytaniem ofertowym / ofertą oraz celem zawarcia umowy sprzedaży lub najmu przedmiotów oferowanych przez Administratora.
Confidentiality Notice: This email, particularly its content and any attached files, may be confidential. If you are not an intended recipient, any disclosure, distribution and reproduction of this message is prohibited. In this case please notify the sender immediately and then delete this message and any attachments."""

SIGNATURE_SEPARATOR = "\n--\nZ wyrazami szacunku\n"


def compose_body(body: str) -> str:
    """Freeze exactly what SMTP will send in its durable outbox record."""
    text = (body or "").rstrip()
    if SIGNATURE_SEPARATOR in text or "Bartłomiej Drężek\nKoordynator magazynu" in text:
        return text
    if text.endswith("Pozdrawiamy"):
        text = text[:-len("Pozdrawiamy")].rstrip()
    return text + "\n\n" + _signature_plain()


def set_message_content(message, body: str) -> None:
    """Render signature as both text and Outlook-friendly HTML/CID."""
    message.set_content(body)
    if SIGNATURE_SEPARATOR not in body:
        return
    prefix = body.split(SIGNATURE_SEPARATOR, 1)[0].strip()
    intro = '<div style="font-family:Arial,sans-serif;font-size:14px;color:#202124;white-space:pre-wrap">' + html.escape(prefix) + '</div>'
    message.add_alternative(intro + _signature_html(), subtype="html")
    image_path = Path(__file__).resolve().parents[1] / "static" / "signature_banner.jpg"
    if image_path.is_file():
        message.get_payload()[1].add_related(
            image_path.read_bytes(),
            maintype="image", subtype="jpeg", cid="<dampol-signature>",
            filename="dampol-signature.jpg", disposition="inline",
        )
