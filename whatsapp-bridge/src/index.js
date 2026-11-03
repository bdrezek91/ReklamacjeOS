const axios = require("axios");
const fs = require("fs");
const FormData = require("form-data");
const http = require("http");
const path = require("path");
const qrcode = require("qrcode-terminal");
const { Client, LocalAuth } = require("whatsapp-web.js");
const { shouldIgnoreMessage } = require("./message_filter");
const { retryDelayMs } = require("./retry");
const { createInboxSpool } = require("./inbox_spool");

const OUTBOX_POLL_INTERVAL_MS = 5000;
const OUTBOX_MAX_RETRY_MS = 60000;
const INBOX_RECOVERY_INTERVAL_MS = 5 * 60 * 1000;
const INBOX_RECOVERY_LIMIT = 300;
const INBOX_RECOVERY_LOOKBACK_DAYS = 14;

const config = {
  backendUrl: (process.env.BACKEND_URL || "http://backend:8000").replace(/\/$/, ""),
  token: process.env.BRIDGE_API_TOKEN || "",
  groupId: process.env.WHATSAPP_GROUP_ID || "",
  clientId: process.env.WHATSAPP_CLIENT_ID || "reklamacje-os",
  sessionPath: process.env.SESSION_PATH || "/app/session",
  maxUploadBytes: Number(process.env.MAX_UPLOAD_BYTES || 15 * 1024 * 1024),
  chromiumPath: process.env.PUPPETEER_EXECUTABLE_PATH || "/usr/bin/chromium",
  proxyServer: process.env.WHATSAPP_PROXY_SERVER || "",
  healthPort: Number(process.env.HEALTH_PORT || 3001),
};

if (!config.token) {
  throw new Error("BRIDGE_API_TOKEN is required");
}

const chromiumArgs = [
  "--no-sandbox",
  "--disable-setuid-sandbox",
  "--disable-dev-shm-usage",
  "--disable-gpu",
  "--no-zygote",
];

if (config.proxyServer) {
  chromiumArgs.push(`--proxy-server=${config.proxyServer}`);
  log("Ruch WhatsApp Web korzysta z proxy podczas parowania");
}

const client = new Client({
  authStrategy: new LocalAuth({ clientId: config.clientId, dataPath: config.sessionPath }),
  puppeteer: {
    executablePath: config.chromiumPath,
    headless: true,
    args: chromiumArgs,
  },
});

const inboxSpool = createInboxSpool(config.sessionPath);
const processing = new Set();
let inboxRecoveryTimer = null;
let inboxRecoveryBusy = false;
let pendingDeliveryBusy = false;
let whatsappReady = false;
let outboxPolling = false;
let outboxTimer = null;
let outboxConsecutiveErrors = 0;
let outboxRetryDelayMs = OUTBOX_POLL_INTERVAL_MS;
let outboxLastBackendSuccessAt = null;

function log(message, metadata = {}) {
  const safe = { ...metadata };
  delete safe.token;
  console.log(JSON.stringify({ time: new Date().toISOString(), message, ...safe }));
}

function clearStaleChromiumLocks() {
  const profilePath = path.join(config.sessionPath, `session-${config.clientId}`);
  let removed = 0;

  for (const name of ["SingletonCookie", "SingletonLock", "SingletonSocket"]) {
    try {
      fs.unlinkSync(path.join(profilePath, name));
      removed += 1;
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }

  if (removed) log("Usunięto nieaktualne blokady profilu Chromium", { count: removed });
}

const healthServer = http.createServer((request, response) => {
  if (request.url !== "/health") {
    response.writeHead(404, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ status: "not_found" }));
    return;
  }

  response.writeHead(whatsappReady ? 200 : 503, { "Content-Type": "application/json" });
  response.end(
    JSON.stringify({
      status: whatsappReady ? "ready" : "starting",
      outbox: {
        backend: outboxConsecutiveErrors ? "retrying" : "ok",
        consecutiveErrors: outboxConsecutiveErrors,
        lastSuccessAt: outboxLastBackendSuccessAt,
        nextPollMs: outboxRetryDelayMs,
      },
    }),
  );
});

healthServer.listen(config.healthPort, "0.0.0.0", () => {
  log("Endpoint zdrowia bridge uruchomiony", { port: config.healthPort });
});

async function printGroups() {
  const chats = await client.getChats();
  const groups = chats
    .filter((chat) => chat.isGroup)
    .map((chat) => ({ name: chat.name, id: chat.id._serialized }))
    .sort((a, b) => a.name.localeCompare(b.name, "pl"));

  log("Dostępne grupy WhatsApp", { count: groups.length });
  for (const group of groups) {
    console.log(`GROUP | ${group.name} | ${group.id}`);
  }

  if (!config.groupId) {
    log("WHATSAPP_GROUP_ID jest pusty. Bridge działa w trybie odkrywania i niczego nie zapisuje.");
  } else if (!groups.some((group) => group.id === config.groupId)) {
    log("Skonfigurowanego WHATSAPP_GROUP_ID nie znaleziono na liście grup", { groupId: config.groupId });
  } else {
    log("Aktywna whitelistowana grupa", { groupId: config.groupId });
  }
}

function fileNameFor(media, messageId) {
  const extensionByMime = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/heif": "heif",
  };
  const extension = extensionByMime[media.mimetype] || "bin";
  return media.filename || `${messageId.replace(/[^A-Za-z0-9_.-]/g, "_")}.${extension}`;
}

async function submitInboxRecord(record) {
  const form = new FormData();
  for (const key of [
    "wa_message_id", "group_id", "group_name", "author_id", "author_name",
    "body", "message_type", "source_timestamp", "source_payload",
  ]) {
    form.append(key, String(record[key] ?? ""));
  }
  if (record.quoted_message_id) form.append("quoted_message_id", record.quoted_message_id);
  if (record.media) {
    form.append("media", Buffer.from(record.media.base64, "base64"), {
      filename: record.media.filename,
      contentType: record.media.mimeType,
    });
  }
  const response = await axios.post(`${config.backendUrl}/api/internal/whatsapp/messages`, form, {
    headers: { ...form.getHeaders(), Authorization: `Bearer ${config.token}` },
    maxBodyLength: config.maxUploadBytes + 1024 * 1024,
    timeout: 60000,
    validateStatus: (status) => status < 500,
  });
  if (response.status >= 400) {
    throw new Error(`Backend rejected message (${response.status}): ${JSON.stringify(response.data)}`);
  }
  inboxSpool.markDelivered(record.wa_message_id);
  log(response.data.created ? "Zapisano wiadomość" : "Wiadomość już istnieje", {
    messageId: record.wa_message_id,
    complaintId: response.data.complaint_id ?? null,
    source: record.recovery_source || "live",
  });
}

async function forwardMessage(message, source = "live") {
  const messageId = message.id?._serialized;
  if (!messageId || processing.has(messageId) || shouldIgnoreMessage(message) ||
      inboxSpool.isDelivered(messageId)) return;
  processing.add(messageId);

  try {
    const chat = await message.getChat();
    if (!chat.isGroup) return;
    const groupId = chat.id._serialized;
    if (!config.groupId || groupId !== config.groupId) return;

    const contact = await message.getContact().catch(() => null);
    let quotedMessageId = null;
    if (message.hasQuotedMsg) {
      const quoted = await message.getQuotedMessage().catch(() => null);
      quotedMessageId = quoted?.id?._serialized || null;
    }

    const record = {
      wa_message_id: messageId,
      group_id: groupId,
      group_name: chat.name || "",
      author_id: message.author || message.from || "",
      author_name: contact?.pushname || contact?.name || contact?.shortName || "",
      body: message.body || "",
      message_type: message.type || "unknown",
      quoted_message_id: quotedMessageId,
      source_timestamp: new Date(message.timestamp * 1000).toISOString(),
      source_payload: JSON.stringify({
        fromMe: Boolean(message.fromMe),
        hasMedia: Boolean(message.hasMedia),
        hasQuotedMsg: Boolean(message.hasQuotedMsg),
        deviceType: message.deviceType || null,
      }),
      recovery_source: source,
    };

    if (message.hasMedia) {
      const media = await message.downloadMedia();
      if (!media) throw new Error("WhatsApp did not return media data");
      if (!media.mimetype.startsWith("image/")) {
        log("Pominięto nieobsługiwane medium; tekst/metadane wiadomości zostaną zapisane", {
          messageId, mimeType: media.mimetype,
        });
      } else {
        const buffer = Buffer.from(media.data, "base64");
        if (buffer.length > config.maxUploadBytes) throw new Error(`Media exceeds configured limit (${buffer.length} bytes)`);
        record.media = {
          base64: media.data,
          filename: fileNameFor(media, messageId),
          mimeType: media.mimetype,
        };
      }
    }

    // Persist before network transmission. An offline backend must not lose the event.
    inboxSpool.persist(record);
    try {
      await submitInboxRecord(record);
    } catch (error) {
      log("Wiadomość zachowana do ponowienia", { messageId, error: error.message });
    }
  } catch (error) {
    log("Błąd przetwarzania wiadomości", { messageId, error: error.message });
  } finally {
    processing.delete(messageId);
  }
}

async function flushInboxSpool() {
  if (pendingDeliveryBusy) return;
  pendingDeliveryBusy = true;
  try {
    const records = inboxSpool.pending(150);
    for (const record of records) {
      if (processing.has(record.wa_message_id)) continue;
      try {
        await submitInboxRecord(record);
      } catch (error) {
        log("Odroczono dostarczenie wiadomości WhatsApp", {
          messageId: record.wa_message_id, error: error.message,
        });
        break;
      }
    }
  } catch (error) {
    log("Błąd trwałej kolejki przychodzącej", { error: error.message });
  } finally {
    pendingDeliveryBusy = false;
  }
}

async function recoverGroupHistory() {
  if (!whatsappReady || inboxRecoveryBusy || !config.groupId) return;
  inboxRecoveryBusy = true;
  try {
    await flushInboxSpool();
    const chat = await client.getChatById(config.groupId);
    const messages = await chat.fetchMessages({ limit: INBOX_RECOVERY_LIMIT });
    const cutoff = Math.floor(Date.now() / 1000) - INBOX_RECOVERY_LOOKBACK_DAYS * 86400;
    let scanned = 0;
    let candidates = 0;
    for (const message of messages.sort((a, b) => a.timestamp - b.timestamp)) {
      if (!message.timestamp || message.timestamp < cutoff) continue;
      scanned++;
      const id = message.id?._serialized;
      if (!id || shouldIgnoreMessage(message) || inboxSpool.isDelivered(id)) continue;
      candidates++;
      await forwardMessage(message, "history");
    }
    await flushInboxSpool();
    log("Sprawdzono historię grupy reklamacyjnej", { scanned, candidates });
  } catch (error) {
    log("Błąd synchronizacji historii grupy reklamacyjnej", { error: error.message });
  } finally {
    inboxRecoveryBusy = false;
  }
}

function startInboxRecovery() {
  if (inboxRecoveryTimer) clearInterval(inboxRecoveryTimer);
  inboxRecoveryTimer = setInterval(() => {
    recoverGroupHistory().catch((error) => log("Błąd historii WhatsApp", { error: error.message }));
  }, INBOX_RECOVERY_INTERVAL_MS);
  recoverGroupHistory().catch((error) => log("Błąd startowej synchronizacji WhatsApp", { error: error.message }));
}

async function reportOutboxResult(itemId, result, data) {
  const form = new URLSearchParams(data);
  await axios.post(`${config.backendUrl}/api/internal/whatsapp/outbox/${itemId}/${result}`, form, {
    headers: { Authorization: `Bearer ${config.token}` },
    timeout: 15000,
  });
}

function markOutboxBackendSuccess() {
  if (outboxConsecutiveErrors) {
    log("Połączenie kolejki WhatsApp z backendem odzyskane", { previousErrors: outboxConsecutiveErrors });
  }
  outboxConsecutiveErrors = 0;
  outboxRetryDelayMs = OUTBOX_POLL_INTERVAL_MS;
  outboxLastBackendSuccessAt = new Date().toISOString();
}

function markOutboxBackendFailure(error) {
  outboxConsecutiveErrors += 1;
  outboxRetryDelayMs = retryDelayMs(outboxConsecutiveErrors, {
    baseMs: OUTBOX_POLL_INTERVAL_MS,
    maxMs: OUTBOX_MAX_RETRY_MS,
  });
  log("Błąd połączenia kolejki WhatsApp z backendem", {
    error: error.message,
    consecutiveErrors: outboxConsecutiveErrors,
    nextRetryMs: outboxRetryDelayMs,
    lastSuccessAt: outboxLastBackendSuccessAt,
  });
}

function scheduleOutboxPoll(delayMs = outboxRetryDelayMs) {
  if (outboxTimer) clearTimeout(outboxTimer);
  outboxTimer = setTimeout(async () => {
    outboxTimer = null;
    await pollOutbox();
    if (whatsappReady) scheduleOutboxPoll(outboxRetryDelayMs);
  }, delayMs);
}

async function pollOutbox() {
  if (!whatsappReady || outboxPolling || !config.groupId) return;
  outboxPolling = true;
  let item = null;
  try {
    const response = await axios.post(`${config.backendUrl}/api/internal/whatsapp/outbox/claim`, null, {
      headers: { Authorization: `Bearer ${config.token}` },
      timeout: 15000,
      validateStatus: (status) => status === 200 || status === 204,
    });
    markOutboxBackendSuccess();
    if (response.status === 204) return;
    item = response.data;

    let sentMessage;
    try {
      sentMessage = await client.sendMessage(item.group_id, item.body);
    } catch (sendError) {
      log("Nie udało się wysłać numeru reklamacji", { outboxId: item.id, error: sendError.message });
      await reportOutboxResult(item.id, "failed", { error: sendError.message }).catch((reportError) => {
        log("Nie udało się zapisać błędu wysyłki", { outboxId: item.id, error: reportError.message });
      });
      return;
    }

    const waMessageId = sentMessage.id?._serialized;
    if (!waMessageId) throw new Error("WhatsApp nie zwrócił ID wysłanej wiadomości");
    try {
      await reportOutboxResult(item.id, "sent", { wa_message_id: waMessageId });
      log("Wysłano numer reklamacji na grupę", { outboxId: item.id, messageId: waMessageId });
    } catch (reportError) {
      log("Numer wysłany, ale nie zapisano potwierdzenia; wymagane sprawdzenie ręczne", {
        outboxId: item.id,
        messageId: waMessageId,
        error: reportError.message,
      });
    }
  } catch (error) {
    if (item) {
      log("Błąd obsługi kolejki numerów reklamacji", { outboxId: item.id, error: error.message });
    } else {
      markOutboxBackendFailure(error);
    }
  } finally {
    outboxPolling = false;
  }
}

client.on("qr", (qr) => {
  console.log("\nZeskanuj QR w WhatsApp: Ustawienia -> Połączone urządzenia -> Połącz urządzenie\n");
  qrcode.generate(qr, { small: true });
});

client.on("authenticated", () => log("WhatsApp uwierzytelniony"));
client.on("auth_failure", (error) => {
  whatsappReady = false;
  if (outboxTimer) clearTimeout(outboxTimer);
  outboxTimer = null;
  log("Błąd uwierzytelnienia WhatsApp", { error });
});
client.on("disconnected", (reason) => {
  whatsappReady = false;
  if (outboxTimer) clearTimeout(outboxTimer);
  outboxTimer = null;
  log("WhatsApp rozłączony", { reason });
});
client.on("ready", async () => {
  whatsappReady = true;
  log("WhatsApp bridge gotowy");
  startInboxRecovery();
  await printGroups().catch((error) => log("Nie udało się pobrać grup", { error: error.message }));
  await pollOutbox();
  scheduleOutboxPoll(outboxRetryDelayMs);
});

// `message` covers incoming messages; `message_create` also covers messages sent by
// the linked account. Both are safe because the backend enforces a unique WhatsApp ID.
client.on("message", forwardMessage);
client.on("message_create", forwardMessage);

process.on("SIGTERM", async () => {
  whatsappReady = false;
  if (outboxTimer) clearTimeout(outboxTimer);
  if (inboxRecoveryTimer) clearInterval(inboxRecoveryTimer);
  log("Zamykanie bridge");
  healthServer.close();
  await client.destroy();
  process.exit(0);
});

clearStaleChromiumLocks();
client.initialize().catch((error) => {
  log("Nie udało się uruchomić WhatsApp", { error: error.message });
  process.exit(1);
});
