const test = require("node:test");
const assert = require("node:assert/strict");

const { shouldIgnoreMessage } = require("../src/message_filter");

test("ignores every message sent by the connected WhatsApp account", () => {
  assert.equal(shouldIgnoreMessage({ fromMe: true, body: "dowolna treść" }), true);
});

test("keeps incoming group messages", () => {
  assert.equal(shouldIgnoreMessage({ fromMe: false, body: "reklamacja" }), false);
  assert.equal(shouldIgnoreMessage({ body: "reklamacja" }), false);
});
