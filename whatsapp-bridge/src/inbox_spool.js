"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

function createInboxSpool(sessionPath) {
  const directory = path.join(sessionPath, "reklamacje-inbox-spool");
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  const deliveredPath = path.join(directory, "delivered.log");
  const delivered = new Set();
  if (fs.existsSync(deliveredPath)) {
    for (const line of fs.readFileSync(deliveredPath, "utf8").split("\n")) {
      const key = line.split(" ")[0];
      if (/^[a-f0-9]{64}$/.test(key)) delivered.add(key);
    }
  }

  function keyFor(id) {
    return crypto.createHash("sha256").update(id).digest("hex");
  }

  function fileFor(id) {
    return path.join(directory, keyFor(id) + ".json");
  }

  function isDelivered(id) {
    return delivered.has(keyFor(id));
  }

  function persist(record) {
    if (!record || !record.wa_message_id) throw new Error("Message ID required");
    if (isDelivered(record.wa_message_id)) return false;
    const file = fileFor(record.wa_message_id);
    if (fs.existsSync(file)) return true;
    const temporary = file + "." + process.pid + ".tmp";
    try {
      fs.writeFileSync(temporary, JSON.stringify(record), { mode: 0o600, flag: "wx" });
      fs.renameSync(temporary, file);
    } finally {
      if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
    }
    return true;
  }

  function markDelivered(id) {
    const key = keyFor(id);
    if (!delivered.has(key)) {
      fs.appendFileSync(deliveredPath, key + " " + Date.now() + "\n", { mode: 0o600 });
      delivered.add(key);
    }
    const file = fileFor(id);
    if (fs.existsSync(file)) fs.unlinkSync(file);
  }

  function pending(limit = 100) {
    const filenames = fs.readdirSync(directory).filter((f) => /^[a-f0-9]{64}\.json$/.test(f));
    const records = [];
    for (const filename of filenames) {
      const file = path.join(directory, filename);
      const record = JSON.parse(fs.readFileSync(file, "utf8"));
      if (!record.wa_message_id || keyFor(record.wa_message_id) + ".json" !== filename) {
        throw new Error("Invalid inbox spool entry: " + filename);
      }
      if (isDelivered(record.wa_message_id)) {
        fs.unlinkSync(file);
      } else {
        records.push(record);
      }
    }
    records.sort((a, b) => (a.source_timestamp || "").localeCompare(b.source_timestamp || ""));
    return records.slice(0, limit);
  }

  return { isDelivered, persist, markDelivered, pending };
}

module.exports = { createInboxSpool };
