'use strict';
/**
 * The website's email renderer: one branded shell, an HTML part and a plain
 * text part from the same blocks, and a hidden preheader.
 *
 *   render(id, vars) → { id, subject, preheader, text, html, security }
 *
 * The words live in ./copy.en.js; this file owns only the layout. It requires
 * nothing else from the app, so the mailer, the account notices, the job
 * script and the preview script can all load it without a database, a secret
 * or a server.
 *
 * WHY IT IS BUILT THIS WAY
 *
 *  - Inbox HTML, not web HTML. Gmail, Outlook and Apple Mail drop <style>
 *    blocks, external CSS, web fonts and SVG in different combinations, so
 *    the shell is nested tables with inline styles only, one column, at most
 *    600px wide, with a single primary button. Georgia stands in for the
 *    site's Cinzel wordmark and Arial for Inter: a web font would be a
 *    remote fetch that most clients refuse anyway.
 *  - The colours are the site's own tokens (app/public/styles.css :root),
 *    dark, with `color-scheme` declared so a client that honours it does not
 *    invert a page that is already dark, and bgcolor attributes for the ones
 *    that ignore CSS backgrounds.
 *  - The mark is a PNG under the public origin (app_icon_256.png) with its
 *    size and alt text, beside a text wordmark, so a mail with images blocked
 *    still says who sent it. SVG and WebP fail in common Outlook versions,
 *    and a CID attachment would change the mailer.
 *  - Every link is built from the `base` the caller passes, which is
 *    mailer.baseUrl() (lib/public_origin.js). Nothing here names a host.
 *  - Values are plain text. HTML output escapes the filled string, so a name,
 *    a URL or a plan feature can never become markup; the text part is not
 *    escaped.
 *  - A placeholder the template uses but the caller did not supply throws.
 *    A mail with an empty verify link is worse than no mail, and the callers
 *    are all best-effort: they catch and log.
 */

const COPY = require('./copy.en');

// app/public/styles.css :root, the committed dark theme. The token names keep
// a historical "--gold" spelling; the accent is rune-blue.
const C = {
  bg: '#080a10',        // --bg
  surface: '#10131c',   // --surface
  line: '#2a3142',      // --line
  accent: '#3fb6ff',    // --gold
  accentBright: '#8fd9ff', // --gold-bright
  text: '#f3f5f8',      // --text
  text2: '#b7c0d0',     // --text-2
  text3: '#96a0b3',     // --text-3
};

const SANS = 'Arial, Helvetica, sans-serif';
const SERIF = "Georgia, 'Times New Roman', serif";

const PATHS = {
  arena_url: '/arena',
  provable_url: '/provable',
  learn_url: '/learn',
  duel_url: '/duel',
  dashboard_url: '/dashboard',
  // The sign-in panel's "Forgot your password?" form, opened by the landing
  // page's #forgot door (public/index.html).
  forgot_url: '/#forgot',
};

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

function _blank(v) {
  return v == null || String(v).trim() === '';
}

/**
 * Fill `{{key}}` and `{{#key}}…{{/key}}` from `vars`. A section renders only
 * when its value is not blank; a bare placeholder with no value throws.
 */
function fill(str, vars) {
  const sections = String(str).replace(/\{\{#(\w+)\}\}([\s\S]*?)\{\{\/\1\}\}/g,
    (_m, key, inner) => (_blank(vars[key]) ? '' : inner));
  return sections.replace(/\{\{(\w+)\}\}/g, (_m, key) => {
    const v = vars[key];
    if (v == null) throw new Error(`email template needs {{${key}}}`);
    return String(v);
  });
}

/** The links every template may use, from the public origin. */
function urlsFor(base) {
  const b = String(base == null ? '' : base).replace(/\/+$/, '');
  const out = { site_url: b };
  for (const [k, p] of Object.entries(PATHS)) out[k] = b + p;
  return out;
}

// A URL inside already-escaped text becomes a link to itself. The last
// character may not be punctuation, so "at https://x/." links "https://x/".
const URL_IN_TEXT = /https?:\/\/[^\s<>"']*[^\s<>"'.,;:!?)]/g;

function _autolink(escaped) {
  return escaped.replace(URL_IN_TEXT,
    (u) => `<a href="${u}" style="color:${C.accentBright};text-decoration:underline;word-break:break-all;">${u}</a>`);
}

const P_STYLE = `margin:0 0 16px;font-family:${SANS};font-size:16px;line-height:1.6;color:${C.text};`;
const NOTE_STYLE = `margin:0 0 16px;font-family:${SANS};font-size:14px;line-height:1.6;color:${C.text3};`;
const LIST_STYLE = `margin:0 0 16px;padding:0 0 0 22px;font-family:${SANS};font-size:16px;line-height:1.6;color:${C.text};`;
const LI_STYLE = 'margin:0 0 8px;';
const LINK_STYLE = `color:${C.accentBright};text-decoration:underline;`;

function _para(text, style = P_STYLE) {
  return `<p style="${style}">${_autolink(escapeHtml(text))}</p>`;
}

function _button(label, url) {
  return '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:8px 0 24px;">'
    + `<tr><td align="center" bgcolor="${C.accent}" style="border-radius:8px;background:${C.accent};">`
    + `<a href="${escapeHtml(url)}" style="display:inline-block;padding:14px 28px;font-family:${SANS};`
    + `font-size:16px;font-weight:bold;line-height:1.2;color:${C.bg};text-decoration:none;border-radius:8px;">`
    + `${escapeHtml(label)}</a></td></tr></table>`;
}

function _features(vars) {
  const list = vars.features;
  if (!Array.isArray(list) || !list.length || list.some(_blank)) {
    throw new Error('email template needs a non-empty features list');
  }
  return list.map(String);
}

/** One block → { html, text }. `null` when an `if` block is switched off. */
function _block(b, vars) {
  if (b.if && vars[b.if] !== true) return null;
  if (b.greet) {
    const t = fill(COPY.GREETING, vars);
    return { html: _para(t), text: t };
  }
  if (b.p != null) {
    const t = fill(b.p, vars);
    return { html: _para(t), text: t };
  }
  if (b.note != null) {
    const t = fill(b.note, vars);
    return { html: _para(t, NOTE_STYLE), text: t };
  }
  if (b.ul || b.features) {
    const items = b.features ? _features(vars) : b.ul.map((x) => fill(x, vars));
    return {
      html: `<ul style="${LIST_STYLE}">`
        + items.map((i) => `<li style="${LI_STYLE}">${_autolink(escapeHtml(i))}</li>`).join('')
        + '</ul>',
      text: items.map((i) => `- ${i}`).join('\n'),
    };
  }
  if (b.ol) {
    const items = b.ol.map((it) => ({
      text: fill(it.text, vars),
      links: (it.links || []).map((l) => fill(l, vars)),
    }));
    return {
      html: `<ol style="${LIST_STYLE}">`
        + items.map((i) => `<li style="${LI_STYLE}">${escapeHtml(i.text)}`
          + i.links.map((l) => `<br><a href="${escapeHtml(l)}" style="${LINK_STYLE}word-break:break-all;">${escapeHtml(l)}</a>`).join('')
          + '</li>').join('')
        + '</ol>',
      text: items.map((i, n) => `${n + 1}. ${i.text}`
        + i.links.map((l) => `\n   ${l}`).join('')).join('\n\n'),
    };
  }
  if (b.cta) {
    const label = fill(b.cta.label, vars);
    const url = fill(b.cta.url, vars);
    return {
      html: b.cta.primary ? _button(label, url)
        : `<p style="${P_STYLE}"><a href="${escapeHtml(url)}" style="${LINK_STYLE}font-weight:bold;">${escapeHtml(label)}</a></p>`,
      text: `${label}\n${url}`,
    };
  }
  throw new Error('email template has a block the renderer does not know');
}

function _footerLines(t, vars) {
  const lines = [];
  if (t.advice) lines.push({ text: COPY.ADVICE });
  const note = t.security ? COPY.SECURITY_NOTE : t.note;
  if (note) lines.push({ text: note });
  lines.push({ text: COPY.CREDIT, credit: true });
  if (!t.security) lines.push({ text: fill(COPY.UNSUBSCRIBE, vars) });
  return lines;
}

function _header(base) {
  // No image without an origin: a relative src is a broken image in an inbox.
  const mark = base
    ? `<td style="padding:0 12px 0 0;vertical-align:middle;"><img src="${escapeHtml(base)}/app_icon_256.png" `
      + 'width="40" height="40" alt="RUNECLAW" style="display:block;border:0;outline:none;'
      + 'text-decoration:none;border-radius:8px;"></td>'
    : '';
  return '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
    + mark
    + `<td style="vertical-align:middle;font-family:${SERIF};font-size:22px;font-weight:bold;`
    + `letter-spacing:0.12em;color:${C.text};">RUNECLAW</td></tr></table>`;
}

// Filler after the preheader, so a client's preview line stops at the
// preheader instead of running on into the first paragraph.
const PREHEADER_FILL = '&#847;&zwnj;&nbsp;'.repeat(40);

function _shell({ subject, preheader, base, bodyHtml, footer }) {
  const footerHtml = footer.map((l) => `<p style="margin:0 0 8px;font-family:${SANS};font-size:12px;`
    + `line-height:1.6;color:${l.credit ? C.text2 : C.text3};${l.credit ? 'letter-spacing:0.08em;' : ''}">`
    + `${_autolink(escapeHtml(l.text))}</p>`).join('');
  return '<!DOCTYPE html>\n'
    + '<html lang="en" xmlns="http://www.w3.org/1999/xhtml">\n<head>\n'
    + '<meta charset="utf-8">\n'
    + '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    + '<meta name="x-apple-disable-message-reformatting">\n'
    + '<meta name="color-scheme" content="dark light">\n'
    + '<meta name="supported-color-schemes" content="dark light">\n'
    + `<title>${escapeHtml(subject)}</title>\n</head>\n`
    + `<body bgcolor="${C.bg}" style="margin:0;padding:0;width:100%;background:${C.bg};color-scheme:dark light;">\n`
    + `<div style="display:none;max-height:0;max-width:0;overflow:hidden;opacity:0;mso-hide:all;font-size:1px;line-height:1px;color:${C.bg};">`
    + `${escapeHtml(preheader)}${PREHEADER_FILL}</div>\n`
    + `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="${C.bg}" style="background:${C.bg};">\n`
    + '<tr><td align="center" style="padding:24px 12px;">\n'
    + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;">\n'
    + `<tr><td style="padding:0 4px 16px;">${_header(base)}</td></tr>\n`
    + `<tr><td bgcolor="${C.surface}" style="background:${C.surface};border:1px solid ${C.line};border-radius:12px;padding:28px 28px 12px;">\n`
    + `${bodyHtml}\n</td></tr>\n`
    + `<tr><td style="padding:20px 4px 0;">${footerHtml}</td></tr>\n`
    + '</table>\n</td></tr>\n</table>\n</body>\n</html>\n';
}

/**
 * Render one template. `vars.base` is the public origin (mailer.baseUrl());
 * the shared links (arena_url, dashboard_url, …) are derived from it, and a
 * caller's own value for one of them wins.
 */
function render(id, vars = {}) {
  const t = Object.prototype.hasOwnProperty.call(COPY.TEMPLATES, id) ? COPY.TEMPLATES[id] : null;
  if (!t) throw new Error(`unknown email template: ${id}`);
  if (vars.base == null) throw new Error('email template needs the public origin (base)');
  const base = String(vars.base).replace(/\/+$/, '');
  const v = { ...urlsFor(base), ...vars, base };
  if (!t.security && _blank(v.unsubscribe_url)) {
    throw new Error(`${id} is not a security mail and needs an unsubscribe link`);
  }
  const subject = fill(t.subject, v);
  const preheader = fill(t.preheader, v);
  // A line break in a header value is a second header.
  if (/[\r\n]/.test(subject) || /[\r\n]/.test(preheader)) {
    throw new Error('an email subject or preheader may not contain a line break');
  }
  const blocks = t.blocks.map((b) => _block(b, v)).filter(Boolean);
  const footer = _footerLines(t, v);
  const text = blocks.map((b) => b.text).join('\n\n')
    + '\n\n' + footer.map((l) => l.text).join('\n') + '\n';
  const html = _shell({
    subject, preheader, base, footer,
    bodyHtml: blocks.map((b) => b.html).join('\n'),
  });
  return { id, subject, preheader, text, html, security: t.security === true };
}

/**
 * A token lifetime as the mail says it: "24 hours", "30 minutes". Derived
 * from the same milliseconds the route enforces, so the sentence cannot
 * outlive a change to the lifetime.
 */
function durationText(ms) {
  if (!Number.isInteger(ms) || ms <= 0 || ms % 60000 !== 0) {
    throw new Error(`not a whole number of minutes: ${ms}`);
  }
  const minutes = ms / 60000;
  if (minutes % 60 === 0) {
    const h = minutes / 60;
    return `${h} hour${h === 1 ? '' : 's'}`;
  }
  return `${minutes} minute${minutes === 1 ? '' : 's'}`;
}

/**
 * When a change happened, as a security notice says it: "2026-10-14 15:32
 * UTC". Unambiguous across the 14 languages the site speaks, and no time
 * zone the reader has to guess.
 */
function whenText(at) {
  const d = at instanceof Date ? at : new Date(at);
  if (Number.isNaN(d.getTime())) throw new Error('not a time');
  return `${d.toISOString().slice(0, 16).replace('T', ' ')} UTC`;
}

/** Is this template a security mail? Throws on an unknown id. */
function isSecurity(id) {
  const t = Object.prototype.hasOwnProperty.call(COPY.TEMPLATES, id) ? COPY.TEMPLATES[id] : null;
  if (!t) throw new Error(`unknown email template: ${id}`);
  return t.security === true;
}

const TEMPLATE_IDS = Object.freeze(Object.keys(COPY.TEMPLATES));

module.exports = {
  render, isSecurity, fill, escapeHtml, urlsFor, durationText, whenText, TEMPLATE_IDS, COPY,
};
