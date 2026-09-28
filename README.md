# ReklamacjeOS

Panel do obsługi reklamacji materiałów z produkcji. Aktualny zakres to **Etap 5**: przyjęcie reklamacji z WhatsApp, ręczna akceptacja, przygotowanie korespondencji oraz kontrolowana wysyłka SMTP.

> `whatsapp-web.js` nie jest oficjalnym API Meta. Zmiany po stronie WhatsApp mogą wymagać aktualizacji bridge'a, a używanie nieoficjalnego klienta wiąże się z ryzykiem wylogowania lub ograniczenia konta.

## Co działa

- QR przy pierwszym połączeniu i trwała sesja `LocalAuth` w wolumenie Dockera,
- wypisanie nazw i ID wszystkich grup po zalogowaniu,
- tryb odkrywania, gdy `WHATSAPP_GROUP_ID` jest pusty,
- ingest wyłącznie jednej whitelisted grupy; prywatne rozmowy i inne grupy są odrzucane przed pobraniem mediów,
- tekst, autor, oryginalny czas, ID wiadomości, reply/quoted ID i obrazy,
- idempotencja przez unikalne `wa_message_id`,
- oryginalne obrazy na dysku i metadane w PostgreSQL,
- prosty panel z Dashboardem i chronologicznym inboxem,
- automatyczne drafty `DRAFT-xxxx`: cytowana wiadomość ma pierwszeństwo, a pozostałe wpisy tego samego autora są łączone w konfigurowalnym oknie czasu,
- ręczne rozdzielanie wiadomości do nowego draftu i nieusuwające scalanie draftów,
- dziennik utworzenia, przypisania, rozdzielenia i scalenia,
- miesięczna numeracja `R/nn/MM/YYYY` nadawana dopiero po ręcznej akceptacji,
- edytowalna karta reklamacji, wybór zdjęć i przejście do statusu „Do akceptacji”,
- wysyłka numeru na grupę WhatsApp z prośbą o oznaczenie reklamowanych płyt,
- kartoteka dostawców z adresem reklamacyjnym; Paneltech korzysta z `reklamacje@paneltech.pl`,
- przypisanie dostawcy, edytowalny szkic wiadomości i wybrane fotografie,
- pobranie wiadomości `.eml` ze zdjęciami do ręcznej wysyłki,
- trwała kolejka SMTP i osobny worker z blokadą podwójnego zlecenia,
- status „Wysłana” dopiero po przyjęciu wiadomości przez serwer SMTP,
- bezpieczne zatrzymanie przy niejednoznacznym wyniku połączenia i ręczne ponowienie jednoznacznych błędów,
- statusy „Gotowa do wysłania”, „Wysłana” i „Zamknięta” oraz wyszukiwanie,
- Caddy z obowiązkowym HTTP Basic Auth,
- migracje Alembic i schemat przygotowany pod reklamacje, audyt, dane AI oraz przyszłe maile.

## Architektura

```text
WhatsApp Web (LocalAuth)
        │ odczyt grupy i wysyłka numeru po akceptacji
        ▼
Node.js bridge ◄── Bearer token ──► FastAPI ──► PostgreSQL
                                      │
                                      └──────► /data/reklamacje (oryginały)

Przeglądarka ── Basic Auth ──► Caddy ──► FastAPI/Jinja
```

Backend i PostgreSQL nie publikują portów na hosta. Jedynym publicznym wejściem jest Caddy na portach 80/443.

## Uruchomienie na VPS

Wymagania: Linux, Docker Engine z pluginem Compose oraz repozytorium sklonowane na VPS.

```bash
git clone https://github.com/bdrezek91/ReklamacjeOS.git
cd ReklamacjeOS
cp .env.example .env
```

Wygeneruj sekrety:

```bash
openssl rand -hex 32
openssl rand -hex 32
docker run --rm caddy:2.8-alpine caddy hash-password --plaintext 'TUTAJ_MOCNE_HASLO_PANELU'
```

W `.env` ustaw osobno wygenerowane wartości jako `POSTGRES_PASSWORD`, hasło także wewnątrz `DATABASE_URL`, `BRIDGE_API_TOKEN` oraz wynik bcrypt jako `PANEL_PASSWORD_HASH`. Hash bcrypt zawiera znaki `$`, dlatego wpisz go w pojedynczym cudzysłowie, np. `PANEL_PASSWORD_HASH='$2a$...'`. Nie commituj `.env`.

### Pierwsze parowanie i wybór grupy

1. Zostaw `WHATSAPP_GROUP_ID=` puste.
2. Uruchom system:

   ```bash
   docker compose up -d --build
   docker compose logs -f whatsapp
   ```

3. Zeskanuj QR: WhatsApp → Ustawienia → Połączone urządzenia → Połącz urządzenie.
4. Po komunikacie `Dostępne grupy WhatsApp` skopiuj dokładne ID właściwej grupy (`...@g.us`).
5. Wpisz je do `.env` jako `WHATSAPP_GROUP_ID`.
6. Odtwórz backend i bridge z nową konfiguracją:

   ```bash
   docker compose up -d --force-recreate backend whatsapp
   docker compose logs -f whatsapp
   ```

Sesja jest w nazwanym wolumenie `whatsapp_session`, więc zwykły restart lub przebudowa kontenera jej nie usuwa. Nie uruchamiaj `docker compose down -v`, jeśli chcesz zachować sesję i dane.

### Panel i HTTPS

Przy `SITE_ADDRESS=:80` panel działa pod adresem IP VPS przez HTTP i wymaga loginu/hasła z `.env`.

Dla HTTPS ustaw rekord DNS na VPS, a następnie:

```dotenv
SITE_ADDRESS=reklamacje.twojadomena.pl
```

Po `docker compose up -d --force-recreate caddy` Caddy automatycznie pobierze certyfikat.

### Kontrolowana wysyłka SMTP

Automatyczna wysyłka pozostaje wyłączona przy `SMTP_ENABLED=false`. Po uzyskaniu danych skrzynki ustaw w `.env` host, port, login i adres nadawcy. Hasło przechowuj wyłącznie w ignorowanym przez Git pliku `.secrets/smtp_password`; skrypt `ops/set-smtp-password.sh` nadaje mu prawa `640`, zapisuje jego grupę w `SMTP_SECRET_GID` i udostępnia odczyt wyłącznie workerowi. Dla portu 587 użyj `SMTP_STARTTLS=true` oraz `SMTP_SSL=false`; dla portu 465 ustaw odwrotnie. Włącz wysyłkę przez `SMTP_ENABLED=true` dopiero po zapisaniu hasła.

Backend zapisuje zatwierdzoną wiadomość i listę załączników w trwałej kolejce. Osobna usługa `email-worker` wysyła dokładnie tę kopię. Reklamacja otrzymuje status „Wysłana” dopiero po przyjęciu wiadomości przez serwer SMTP. Błąd jednoznaczny można ponowić ręcznie; po zerwaniu połączenia z niejednoznacznym wynikiem system wymaga sprawdzenia skrzynki „Wysłane”, aby nie utworzyć duplikatu.

## Dane i kopie zapasowe

- PostgreSQL: wolumen `postgres_data`.
- Zdjęcia: wolumen `complaint_data`, logicznie `/data/reklamacje/<rok>/INBOX/whatsapp/<id>/original/`.
- Sesja WhatsApp: wolumen `whatsapp_session`.

Kopia zapasowa musi obejmować bazę oraz `complaint_data`. Sam backup PostgreSQL nie zawiera zdjęć.

Usługa `backup` wykonuje `pg_dump` i synchronizuje zdjęcia co 24 godziny bez zatrzymywania aplikacji. Nie usuwa starszych dumpów ani plików. Jej healthcheck przechodzi w stan `unhealthy`, gdy ostatnia kopia ma ponad 30 godzin lub wolne miejsce spadnie poniżej skonfigurowanego progu.

## Grupowanie Etapu 2

Każda nowa wiadomość otrzymuje draft w tej samej transakcji co zapis źródła:

1. Jeżeli cytuje zapisaną wiadomość, trafia do jej aktywnego draftu.
2. W przeciwnym razie trafia do ostatniego aktywnego draftu tego samego autora i grupy, jeżeli mieści się w `GROUPING_WINDOW_MINUTES`.
3. Jeżeli żadna reguła nie pasuje, powstaje kolejny `DRAFT-xxxx`.

Draft nie ma numeru reklamacji. Po przejściu do statusu „Do akceptacji” operator klika „Akceptuj i wyślij numer”. System atomowo nadaje numer `R/nn/MM/YYYY`, zmienia status i zapisuje komunikat w kolejce. Licznik zaczyna się od `01` w każdym miesiącu i jest chroniony blokadą transakcyjną. Bridge wysyła na grupę prośbę o oznaczenie reklamowanych płyt tym numerem, a własnego komunikatu nie zapisuje ponownie jako zgłoszenia.

Operacje ręczne wymagają `PANEL_ACTION_TOKEN`. Rozdzielenie przenosi wybrane wiadomości do nowego draftu. Scalenie przenosi wszystkie wiadomości do draftu docelowego, zachowuje rekord źródłowego draftu i zapisuje zdarzenia audytowe po obu stronach.

## Diagnostyka

```bash
docker compose ps
docker compose logs --tail=200 backend
docker compose logs --tail=200 whatsapp
docker compose logs --tail=200 caddy
curl -u 'admin:HASLO' http://ADRES_VPS/health
```

Jeżeli bridge nie pokazuje QR, usuń wyłącznie wolumen sesji dopiero po świadomej decyzji o ponownym parowaniu.

## Testy lokalne

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
DATABASE_URL=sqlite:///./test.db pytest -q
ruff check app tests alembic

cd ../whatsapp-bridge
npm ci --ignore-scripts
npm run check

cd ..
docker compose --env-file .env.example config --quiet
docker compose build
```

Testy backendu obejmują odrzucenie obcej grupy, idempotencję wiadomości i zapis oryginalnego obrazu.

## Zakres kolejnych etapów

- **Etap 2:** deterministyczne grupowanie wiadomości i zdjęć w `DRAFT-xxxx`, ręczne łączenie i rozdzielanie.
- **Etap 3:** pełna karta reklamacji, galeria, historia zmian, numeracja `R/nn/MM/YYYY` i powiadomienie WhatsApp po akceptacji.
- **Etap 4:** kartoteka dostawców, edytowalny szkic, plik `.eml`, ręczne potwierdzenie wysyłki i zamknięcie.
- **Etap 5 (aktualny):** kontrolowana wysyłka SMTP przyciskiem „AKCEPTUJ I WYŚLIJ”, kolejka i audyt. Bez IMAP.
- **Etap 6:** AI/Vision/OCR z rozdzieleniem source data, AI interpretation i approved data.
