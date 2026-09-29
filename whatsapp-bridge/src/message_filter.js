function shouldIgnoreMessage(message) {
  return Boolean(message?.fromMe);
}

module.exports = { shouldIgnoreMessage };
