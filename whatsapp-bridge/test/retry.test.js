const assert = require("node:assert/strict");
const test = require("node:test");
const { retryDelayMs } = require("../src/retry");

test("uses exponential delay capped at the configured maximum", () => {
  assert.equal(retryDelayMs(1), 5000);
  assert.equal(retryDelayMs(2), 10000);
  assert.equal(retryDelayMs(3), 20000);
  assert.equal(retryDelayMs(4), 40000);
  assert.equal(retryDelayMs(5), 60000);
  assert.equal(retryDelayMs(20), 60000);
});

test("uses the base delay before the first error", () => {
  assert.equal(retryDelayMs(0), 5000);
  assert.equal(retryDelayMs(undefined), 5000);
});

test("supports explicit timing limits", () => {
  assert.equal(retryDelayMs(1, { baseMs: 1000, maxMs: 8000 }), 1000);
  assert.equal(retryDelayMs(5, { baseMs: 1000, maxMs: 8000 }), 8000);
});
