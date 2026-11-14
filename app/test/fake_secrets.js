'use strict';

// Obvious fake credentials for tests, assembled at runtime so the tree
// never contains a contiguous Telegram bot token, Google API key, or
// other provider-pattern literal.

const FAKE_TELEGRAM_BOT_ID = '123456789';
const FAKE_TELEGRAM_BOT_SECRET = 'AA' + 'TESTFAKE' + 'TOKENPLACEHOLDER' + '0'.repeat(9);
const FAKE_GOOGLE_API_KEY = 'AIza' + 'Sy' + 'TESTNOTAREAL' + 'GOOGLEAPIKEY' + '0'.repeat(9);

function joinSecret(...parts) {
  return parts.join('');
}

function fakeTelegramBotToken(botId = FAKE_TELEGRAM_BOT_ID, secret = FAKE_TELEGRAM_BOT_SECRET) {
  return joinSecret(botId, ':', secret);
}

function fakeOpenaiKey() {
  return joinSecret('sk-', 'proj-', 'TESTFAKE', 'OPENAIKEY', '0'.repeat(16));
}

function fakeAnthropicKey() {
  return joinSecret('sk-', 'ant-', 'api03-', 'TESTFAKE', 'ANTHROPICKEY', '0'.repeat(12));
}

function fakeXaiKey() {
  return joinSecret('xai-', 'TESTFAKE', 'XAITOKENPLACEHOLDER', '0'.repeat(8));
}

function fakeJwt() {
  return joinSecret(
    'eyJ', 'hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9',
    '.',
    'eyJ', 'zdWIiOiIxMjM0NTY3ODkwIn0',
    '.',
    'TEST', 'SIGNATUREPLACEHOLDER00',
  );
}

module.exports = {
  FAKE_TELEGRAM_BOT_ID,
  FAKE_TELEGRAM_BOT_SECRET,
  FAKE_GOOGLE_API_KEY,
  joinSecret,
  fakeTelegramBotToken,
  fakeOpenaiKey,
  fakeAnthropicKey,
  fakeXaiKey,
  fakeJwt,
};
