'use strict';

/**
 * Public signals for one bot name. The catalog is bot/botcheck/catalog.json,
 * the same file the Daily Duel bot reads. A submission is one review line.
 * Nothing here holds a key, signs, or stores a Telegram id.
 */

const fs = require('fs');
const path = require('path');

const FLAG = 'BOTCHECK_ENABLED';
const ON_VALUES = new Set(['1', 'true', 'yes', 'on']);
const SIGNAL_IDS = ['creates_wallets', 'asks_seed', 'code_public', 'signs_from', 'public_incident'];
const YES_NO = new Set(['yes', 'no', 'unknown']);
const SIGNING = new Set(['bot_controlled', 'user_wallet', 'unknown']);
const NAME = /^[A-Za-z0-9][A-Za-z0-9 .@_-]{0,63}$/;
const DIGITS = /\d{5,}/;
const BANNED = /\b(?:safe|scam|verified|trusted)\b/i;
const MONEY = /\$|RCLAW|%|\bstake\b/i;
const SIGNING_TEXT = {
  bot_controlled: 'a wallet the bot can sign',
  user_wallet: "the user's own wallet",
  unknown: 'unknown',
};

const ROOT = path.resolve(__dirname, '..', '..');
const CATALOG_PATH = path.join(ROOT, 'bot', 'botcheck', 'catalog.json');

class CatalogError extends Error {
  constructor() {
    super('catalog');
    this.name = 'CatalogError';
  }
}

function enabled(env) {
  const src = env || process.env;
  const raw = String(src[FLAG] == null ? '' : src[FLAG]).trim().toLowerCase();
  return ON_VALUES.has(raw);
}

function queuePath(env) {
  const src = env || process.env;
  const custom = String(src.BOTCHECK_QUEUE_PATH || '').trim();
  if (custom) return path.isAbsolute(custom) ? custom : path.join(ROOT, custom);
  const base = String(src.DAILY_DUEL_STATE_DIR || '').trim();
  if (base) {
    const root = path.isAbsolute(base) ? base : path.join(ROOT, base);
    return path.join(root, 'botcheck_review.jsonl');
  }
  return path.join(ROOT, 'data', 'botcheck', 'review.jsonl');
}

function stamp() {
  return new Date().toISOString();
}

function normalize(query) {
  let text = String(query || '').trim().toLowerCase().replace(/\s+/g, ' ');
  if (text.startsWith('@')) text = text.slice(1).trim();
  return text;
}

function answerText(signalId, answer) {
  if (signalId === 'signs_from') return SIGNING_TEXT[answer];
  return answer;
}

function requireOk(cond) {
  if (!cond) throw new CatalogError();
}

function validate(cat) {
  const asOf = cat.as_of;
  requireOk(typeof asOf === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(asOf));
  requireOk(cat.ai_written_label === 'AI-written');
  const notice = cat.ai_written_notice;
  requireOk(typeof notice === 'string' && notice.startsWith('AI-written') && notice.includes('Art. 50'));
  const disclaimer = cat.disclaimer;
  requireOk(typeof disclaimer === 'string' && disclaimer.includes('Unknown stays unknown') && disclaimer.includes(asOf));
  const specs = cat.signals;
  requireOk(Array.isArray(specs) && specs.map((s) => s && s.id).join() === SIGNAL_IDS.join());
  for (const spec of specs) {
    requireOk(spec && typeof spec.question === 'string' && spec.question.trim());
  }
  const bots = cat.bots;
  requireOk(Array.isArray(bots) && bots.length >= 20);
  const seen = new Set();
  for (const bot of bots) {
    requireOk(bot && typeof bot === 'object');
    const bid = bot.id;
    requireOk(typeof bid === 'string' && !seen.has(bid) && /^[a-z0-9-]{2,64}$/.test(bid));
    seen.add(bid);
    requireOk(typeof bot.name === 'string' && bot.name.trim());
    for (const key of ['aliases', 'usernames']) {
      const vals = bot[key];
      requireOk(Array.isArray(vals) && vals.every((v) => typeof v === 'string' && v.trim()));
    }
    const signals = bot.signals;
    requireOk(signals && typeof signals === 'object' && !Array.isArray(signals));
    const keys = Object.keys(signals);
    requireOk(keys.length === SIGNAL_IDS.length && SIGNAL_IDS.every((id) => Object.prototype.hasOwnProperty.call(signals, id)));
    for (const sid of SIGNAL_IDS) {
      const row = signals[sid];
      requireOk(row && typeof row === 'object');
      const allowed = sid === 'signs_from' ? SIGNING : YES_NO;
      requireOk(allowed.has(row.answer));
      requireOk(typeof row.detail === 'string' && row.detail.trim());
      const url = row.source_url;
      requireOk(typeof url === 'string' && url.startsWith('https://') && !url.includes(' '));
      requireOk(typeof row.source_label === 'string' && row.source_label.trim());
      requireOk(row.as_of === asOf);
      if (sid === 'code_public') {
        requireOk(YES_NO.has(row.maintained));
        if (row.maintained_source_url != null) {
          const extra = row.maintained_source_url;
          requireOk(typeof extra === 'string' && extra.startsWith('https://') && !extra.includes(' '));
          requireOk(typeof row.maintained_source_label === 'string' && row.maintained_source_label.trim());
        }
      } else {
        requireOk(!Object.prototype.hasOwnProperty.call(row, 'maintained'));
      }
    }
  }
}

function load(file) {
  const src = file || CATALOG_PATH;
  let raw;
  try {
    raw = JSON.parse(fs.readFileSync(src, 'utf8'));
  } catch (err) {
    throw new CatalogError();
  }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new CatalogError();
  validate(raw);
  return raw;
}

function index(cat) {
  const out = new Map();
  for (const bot of cat.bots) {
    const keys = [bot.id, bot.name].concat(bot.aliases || [], bot.usernames || []);
    for (const key of keys) {
      const norm = normalize(key);
      if (norm && !out.has(norm)) out.set(norm, bot);
    }
  }
  return out;
}

function summaryText(asOf, signals) {
  const bits = ['Public signals as of ' + asOf + '.'];
  for (const row of signals) {
    if (row.id === 'code_public') {
      bits.push('Code public: ' + row.answer_text + '. Maintained: ' + row.maintained + '.');
    } else if (row.id === 'signs_from') {
      bits.push('Signing: ' + row.answer_text + '.');
    } else {
      bits.push(row.question + ' ' + row.answer_text + '.');
    }
  }
  return bits.join(' ');
}

function toPublic(bot, cat) {
  const specs = {};
  for (const spec of cat.signals) specs[spec.id] = spec.question;
  const signals = [];
  for (const sid of SIGNAL_IDS) {
    const raw = bot.signals[sid];
    const item = {
      id: sid,
      question: specs[sid],
      answer: raw.answer,
      answer_text: answerText(sid, raw.answer),
      detail: raw.detail,
      source_url: raw.source_url,
      source_label: raw.source_label,
      as_of: raw.as_of,
    };
    if (sid === 'code_public') {
      item.maintained = raw.maintained;
      if (raw.maintained_source_url) {
        item.maintained_source_url = raw.maintained_source_url;
        item.maintained_source_label = raw.maintained_source_label;
      }
    }
    signals.push(item);
  }
  return {
    id: bot.id,
    name: bot.name,
    usernames: Array.isArray(bot.usernames) ? bot.usernames.slice() : [],
    signals: signals,
    summary: {
      text: summaryText(String(cat.as_of), signals),
      label: cat.ai_written_label,
      notice: cat.ai_written_notice,
    },
  };
}

function publicDocument(catalog) {
  const cat = catalog != null ? catalog : load();
  return {
    ok: true,
    as_of: cat.as_of,
    disclaimer: cat.disclaimer,
    ai_written_label: cat.ai_written_label,
    ai_written_notice: cat.ai_written_notice,
    bots: cat.bots.map((bot) => toPublic(bot, cat)),
  };
}

function lookup(query, catalog) {
  const text = String(query || '');
  if (BANNED.test(text) || MONEY.test(text)) return { ok: false, error: 'That name cannot be checked.' };
  let cat;
  try {
    cat = catalog != null ? catalog : load();
  } catch (err) {
    if (err instanceof CatalogError || err.name === 'CatalogError') {
      return { ok: false, error: 'The check could not be read.' };
    }
    throw err;
  }
  const norm = normalize(query);
  if (!norm) return { ok: false, error: 'Send a bot name.' };
  const bot = index(cat).get(norm) || null;
  return {
    ok: true,
    as_of: cat.as_of,
    disclaimer: cat.disclaimer,
    query: norm,
    found: bot != null,
    bot: bot ? toPublic(bot, cat) : null,
  };
}

function submissionError(query, note) {
  if (BANNED.test(query) || BANNED.test(note) || MONEY.test(query) || MONEY.test(note)) {
    return 'That note cannot be queued.';
  }
  const text = String(query || '').trim().replace(/\s+/g, ' ');
  if (!text) return 'Send a bot name.';
  if (DIGITS.test(text) || DIGITS.test(String(note || ''))) {
    return 'Send a bot name, not a numeric id. Nothing was stored.';
  }
  if (!NAME.test(text)) return 'Send a bot name.';
  if (String(note || '').length > 200) return 'Send a bot name.';
  return null;
}

function enqueue(query, opts) {
  const o = opts || {};
  const cleaned = String(o.note || '').replace(/\n/g, ' ').trim();
  const problem = submissionError(query, cleaned);
  if (problem) return { ok: false, queued: false, error: problem };
  let cat;
  try {
    cat = o.catalog != null ? o.catalog : load();
  } catch (err) {
    if (err instanceof CatalogError || err.name === 'CatalogError') {
      return { ok: false, queued: false, error: 'The check could not be read.' };
    }
    throw err;
  }
  if (index(cat).get(normalize(query))) {
    return {
      ok: true,
      queued: false,
      error: 'That name is already in the curated list. Nothing was added.',
    };
  }
  const shown = String(query || '').trim().replace(/\s+/g, ' ');
  const via = o.via === 'web' || o.via === 'telegram' ? o.via : 'web';
  const record = { submitted_at: o.now || stamp(), query: shown, via: via };
  if (cleaned) record.note = cleaned;
  const dest = o.queue || queuePath(o.env);
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.appendFileSync(dest, JSON.stringify(record) + '\n', 'utf8');
  return { ok: true, queued: true };
}

module.exports = {
  FLAG: FLAG,
  CatalogError: CatalogError,
  enabled: enabled,
  load: load,
  lookup: lookup,
  publicDocument: publicDocument,
  enqueue: enqueue,
  queuePath: queuePath,
  normalize: normalize,
};
