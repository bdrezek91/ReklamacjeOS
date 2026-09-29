function retryDelayMs(consecutiveErrors, { baseMs = 5000, maxMs = 60000 } = {}) {
  if (!Number.isInteger(consecutiveErrors) || consecutiveErrors < 1) return baseMs;
  const exponent = Math.min(consecutiveErrors - 1, 30);
  return Math.min(baseMs * 2 ** exponent, maxMs);
}

module.exports = { retryDelayMs };
