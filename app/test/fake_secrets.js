'use strict';

// Obvious fake credentials for tests, assembled at runtime so the tree
// never contains a contiguous Telegram bot token or Google API key literal.

const FAKE_TELEGRAM_BOT_ID = '123456789';
const FAKE_TELEGRAM_BOT_SECRET = 'AA' + 'TESTFAKE' + 'TOKENPLACEHOLDER' + '0'.repeat(9);
const FAKE_GOOGLE_API_KEY = 'AIza' + 'Sy' + 'TESTNOTAREAL' + 'GOOGLEAPIKEY' + '0'.repeat(9);

function fakeTelegramBotToken(botId = FAKE_TELEGRAM_BOT_ID, secret = FAKE_TELEGRAM_BOT_SECRET) {
  return `${botId}:${secret}`;
}

module.exports = {
  FAKE_TELEGRAM_BOT_ID,
  FAKE_TELEGRAM_BOT_SECRET,
  FAKE_GOOGLE_API_KEY,
  fakeTelegramBotToken,
};
