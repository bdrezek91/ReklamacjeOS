const axios = require("axios");
const fs = require("fs");
const FormData = require("form-data");
const http = require("http");
const path = require("path");
const qrcode = require("qrcode-terminal");
const { Client, LocalAuth } = require("whatsapp-web.js");

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

const processing = new Set();
let whatsappReady = false;
let outboxPolling = false;
let outboxTimer = null;

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
  response.end(JSON.stringify({ status: whatsappReady ? "ready" : "starting" }));
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

async function forwardMessage(message) {
  const messageId = message.id?._serialized;
  if (!messageId || processing.has(messageId)) return;
  if (
    message.fromMe &&
    /^Przyjęto reklamację nr R\/\d+\/\d{2}\/\d{4}\. Proszę opisać reklamowane płyty: reklamacja nr R\/\d+\/\d{2}\/\d{4}\.$/.test(
      message.body || "",
    )
  ) {
    return;
  }
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

    const form = new FormData();
    form.append("wa_message_id", messageId);
    form.append("group_id", groupId);
    form.append("group_name", chat.name || "");
    form.append("author_id", message.author || message.from || "");
    form.append("author_name", contact?.pushname || contact?.name || contact?.shortName || "");
    form.append("body", message.body || "");
    form.append("message_type", message.type || "unknown");
    if (quotedMessageId) form.append("quoted_message_id", quotedMessageId);
    form.append("source_timestamp", new Date(message.timestamp * 1000).toISOString());
    form.append(
      "source_payload",
      JSON.stringify({
        fromMe: Boolean(message.fromMe),
        hasMedia: Boolean(message.hasMedia),
        hasQuotedMsg: Boolean(message.hasQuotedMsg),
        deviceType: message.deviceType || null,
      }),
    );

    if (message.hasMedia) {
      const media = await message.downloadMedia();
      if (!media) throw new Error("WhatsApp did not return media data");
      if (!media.mimetype.startsWith("image/")) {
        log("Pominięto nieobsługiwane medium; tekst/metadane wiadomości zostaną zapisane", {
          messageId,
          mimeType: media.mimetype,
        });
      } else {
        const buffer = Buffer.from(media.data, "base64");
        if (buffer.length > config.maxUploadBytes) {
          throw new Error(`Media exceeds configured limit (${buffer.length} bytes)`);
        }
        form.append("media", buffer, { filename: fileNameFor(media, messageId), contentType: media.mimetype });
      }
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
    log(response.data.created ? "Zapisano wiadomość" : "Wiadomość już istnieje", { messageId });
  } catch (error) {
    log("Błąd przetwarzania wiadomości", { messageId, error: error.message });
  } finally {
    processing.delete(messageId);
  }
}

async function reportOutboxResult(itemId, result, data) {
  const form = new URLSearchParams(data);
  await axios.post(`${config.backendUrl}/api/internal/whatsapp/outbox/${itemId}/${result}`, form, {
    headers: { Authorization: `Bearer ${config.token}` },
    timeout: 15000,
  });
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
    log("Błąd obsługi kolejki numerów reklamacji", { outboxId: item?.id, error: error.message });
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
  log("Błąd uwierzytelnienia WhatsApp", { error });
});
client.on("disconnected", (reason) => {
  whatsappReady = false;
  log("WhatsApp rozłączony", { reason });
});
client.on("ready", async () => {
  whatsappReady = true;
  log("WhatsApp bridge gotowy");
  await printGroups().catch((error) => log("Nie udało się pobrać grup", { error: error.message }));
  await pollOutbox();
  outboxTimer = setInterval(pollOutbox, 5000);
});

// `message` covers incoming messages; `message_create` also covers messages sent by
// the linked account. Both are safe because the backend enforces a unique WhatsApp ID.
client.on("message", forwardMessage);
client.on("message_create", forwardMessage);

process.on("SIGTERM", async () => {
  whatsappReady = false;
  if (outboxTimer) clearInterval(outboxTimer);
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
