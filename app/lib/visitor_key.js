'use strict';
/**
 * An opaque key per anonymous visitor, for the public chat's daily count.
 *
 * The bot counts each visitor's model-answered questions per UTC day
 * (`bot/web/public_chat_quota.py`), and the address is what tells two
 * visitors apart. The address itself never crosses: the bot is sent an HMAC
 * of it under a key drawn when this process starts, cut to 32 hex
 * characters. So the bot can tell one visitor from another and cannot say
 * who either is, and nothing it logs or holds is an address.
 *
 * The key is per process. A restart of this server draws a new one, every
 * address maps to a new visitor, and every count starts again; a restart of
 * the bot clears its counts too. Both are stated where the limit is
 * documented (.env.example, PUBLIC_CHAT_DAILY_LIMIT).
 *
 * Both doors into the public chat send it — routes/public_chat.js and the MCP
 * `ask_runeclaw` tool — so one address is one count across the two.
 */

const crypto = require('crypto');

const KEY = crypto.randomBytes(32);

/**
 * The visitor key for an address, or null when there is no address to key
 * on. `ipKey` answers 'unknown' for a request with none; every such request
 * sharing one key would put unrelated visitors on one count, so the bot is
 * sent no key and says it counted nothing.
 */
function visitorKey(ip) {
  const addr = typeof ip === 'string' ? ip.trim() : '';
  if (!addr || addr === 'unknown') return null;
  return crypto.createHmac('sha256', KEY).update(addr).digest('hex').slice(0, 32);
}

module.exports = { visitorKey };
