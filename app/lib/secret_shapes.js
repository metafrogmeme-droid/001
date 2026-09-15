'use strict';
/**
 * The one vocabulary of secret shapes, read here rather than re-read.
 *
 * `bot/utils/secret_shapes.py` is the source and says so in its own docstring:
 * eight rows, each carrying the example it must scrub and the decoy it must
 * leave alone. That module's coverage note recorded this runtime as the gap —
 * "`app/lib/safe_error.js` has its own vocabulary — wider on labels, narrower
 * on token shapes — and a second file read by two runtimes is filed, not done"
 * — and what the second vocabulary did not know was expensive. Driven against
 * the website's scrub before this file existed, every one of these was
 * published verbatim:
 *
 *     7123456789:AAHf…                    a Telegram bot token
 *     sk-proj-… · xai-…                   a bare provider key
 *     eyJ….eyJ….SflKxwRJ…                 a session JWT
 *     RUNECLAW_SECRETS_KEY=…              the key that opens the vault
 *     WEB3_SIGNER_PRIVATE_KEY=…           the key that signs transactions
 *     WEB_CREDS_KEY=…
 *     api key: bg_1234…                   the prose spelling
 *
 * and `Authorization: Bearer sk-ant-…` came back as
 * `Authorization: ***REDACTED*** sk-ant-…`: the label eaten, the key printed,
 * under a marker that reads as proof the line was scrubbed.
 *
 * `secret_shapes.generated.json` is rendered from the Python table by
 * `scripts/render_secret_shapes.py` and compared byte for byte by
 * `tests/test_the_website_reads_this_vocabulary.py`, so the rows cannot go
 * stale here — the rule the README command tables already follow. This module
 * compiles them; it invents none.
 *
 * FAIL-CLOSED. A table that cannot be read is not an empty table: the require
 * throws, the route module fails to load, and the server does not start. A
 * scrubber that silently becomes a no-op is the failure this file exists to
 * prevent, and it would be invisible from every response it let through.
 */

const TABLE = require('./secret_shapes.generated.json');

const REDACTED = String((TABLE && TABLE.redacted) || '***REDACTED***');

/**
 * A value worth redacting behind a label, as opposed to a status word.
 *
 * The Python twin, rule for rule: `WEB_CREDS_KEY=required` and
 * `api key: not configured` are sentences ABOUT a key, not a key. It is also
 * what keeps a scheme word out of a redaction — "Bearer" carries no digit and
 * is six characters, so a label row leaves it where it stands and the bearer
 * row takes the token after it.
 */
function looksLikeCredential(value) {
  const v = String(value == null ? '' : value);
  return /\d/.test(v) || v.length >= 20;
}

function compile(row) {
  const flags = row.ignorecase ? 'gi' : 'g';
  const re = new RegExp(row.pattern, flags);
  const spec = row.replacement || {};
  if (spec.kind === 'redact') {
    return { name: row.name, apply: (s) => s.replace(re, REDACTED) };
  }
  if (spec.kind === 'template') {
    // Python spells a backreference \1; JavaScript spells it $1. Same row.
    const template = String(spec.template).replace(/\\(\d)/g, '$$$1');
    return { name: row.name, apply: (s) => s.replace(re, template) };
  }
  if (spec.kind === 'keep_words') {
    const label = Number(spec.label);
    const value = Number(spec.value);
    const sep = String(spec.sep == null ? '=' : spec.sep);
    return {
      name: row.name,
      apply: (s) => s.replace(re, (...args) => {
        const whole = args[0];
        const groups = args.slice(1, args.length - 2);
        const v = groups[value - 1];
        if (!looksLikeCredential(v)) return whole;
        return `${groups[label - 1]}${sep}${REDACTED}`;
      }),
    };
  }
  throw new Error(`secret shape ${row.name}: unknown replacement kind ${spec.kind}`);
}

/**
 * The compiled rows of a table, or a throw.
 *
 * Exported so the fail-closed case can be DRIVEN rather than asserted from the
 * source: a table with no rows is not an empty vocabulary, it is a vocabulary
 * nobody could read, and a scrubber that quietly became a no-op would be
 * invisible from every response it let through.
 */
function buildShapes(doc) {
  if (!doc || !Array.isArray(doc.rows) || doc.rows.length === 0) {
    throw new Error('secret shape table holds no rows — run scripts/render_secret_shapes.py');
  }
  return doc.rows.map(compile);
}

const SHAPES = buildShapes(TABLE);

/** `text` with every shape in the shared table redacted, in table order. */
function scrubSecrets(text) {
  let out = String(text == null ? '' : text);
  for (const shape of SHAPES) out = shape.apply(out);
  return out;
}

module.exports = { SHAPES, ROWS: TABLE.rows, REDACTED, looksLikeCredential, scrubSecrets, buildShapes };
