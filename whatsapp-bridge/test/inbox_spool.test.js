"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createInboxSpool } = require("../src/inbox_spool");

test("incoming WhatsApp spool is durable, idempotent and supports acknowledgement", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "reklamacje-inbox-"));
  try {
    const one = createInboxSpool(dir);
    const record = { wa_message_id: "false_group_uniqueA", source_timestamp: "2026-11-03T09:00:00.000Z", body: "Reklamacja" };
    assert.equal(one.persist(record), true);
    assert.equal(one.persist(record), true);
    assert.equal(one.pending().length, 1);
    const second = createInboxSpool(dir);
    assert.deepEqual(second.pending()[0], record);
    second.markDelivered(record.wa_message_id);
    assert.equal(second.pending().length, 0);
    const third = createInboxSpool(dir);
    assert.equal(third.isDelivered(record.wa_message_id), true);
    assert.equal(third.persist(record), false);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
