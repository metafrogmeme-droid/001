/**
 * The $RCLAW presale's phase, and the bar every page shows while the sale is live.
 *
 * ONE READING. Where an announced presale stands — 'announced' (before it opens), 'live' or
 * 'ended' — is read from the record's sale window (opens_at, closes_at: the two instants Create
 * fixed on chain) and the clock. The /token page (token-page.js), the home strip and this bar
 * all ask presalePhase here, and the bot asks the same question in its own language
 * (bot/formatters/token_card.py presale_phase); tests/fixtures/presale_phase_cases.json holds
 * the two to one answer. A window that cannot be read is the record's own word, 'announced',
 * which is true of a sale that has opened too, and states no time. The hard cap can end the
 * sale before closes_at, which nothing here can see without the chain, so every sentence that
 * names the close says "or until the hard cap" or links to the page that does.
 *
 * THE BAR. While the sale is live, a slim bar at the top of the page names it, shows the closing
 * time and links to /token, where the terms and the one official sale link are. It is not shown
 * on /token itself (the page says all of it), before the sale opens or after it closes, or to a
 * visitor who closed it for this sale (kept in this browser; a later sale shows again). A record
 * that cannot be read shows no bar: an advert that cannot say when the sale closes is one the
 * site does not need to make.
 *
 * Dual export: browser (window.Presale) + Node (require), so the tests drive what the page runs.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.Presale = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /** A sale-window instant as the record writes it: a whole minute in UTC. */
  const WINDOW_AT = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}):00Z$/;
  const BAR_ID = 'presale-bar';
  const DISMISS_KEY = 'rc_presale_bar_closed';
  // The home strip's sentence, one string in fourteen languages for both.
  const BAR_TEXT = '$RCLAW presale is live now';
  const BAR_CTA = 'Terms and the official sale link →';
  const BAR_CLOSE = 'Close';
  const BAR_CLOSES = 'Closes';

  /** Milliseconds for a window instant, or null: not a whole UTC minute, or a date that does not exist. */
  function windowMs(v) {
    const m = typeof v === 'string' ? WINDOW_AT.exec(v) : null;
    if (!m) return null;
    const ms = Date.parse(v);
    if (!Number.isFinite(ms)) return null;
    // Date.parse rolls 2026-02-30 over to 2 March; a date that does not exist is unreadable.
    return new Date(ms).toISOString().slice(0, 16) === m[1] + 'T' + m[2] ? ms : null;
  }

  /** "2026-10-29T14:00:00Z" -> "2026-10-29 14:00 UTC": digits, so no language has to translate a month. */
  function windowText(v) {
    const m = WINDOW_AT.exec(v);
    return m[1] + ' ' + m[2] + ' UTC';
  }

  /** [opens, closes] in milliseconds, or null: either instant unreadable, or a close not after the open. */
  function saleWindow(p) {
    const opens = windowMs(p.opens_at);
    const closes = windowMs(p.closes_at);
    return (opens === null || closes === null || closes <= opens) ? null : [opens, closes];
  }

  function presalePhase(presale, nowMs) {
    const p = (presale && typeof presale === 'object') ? presale : {};
    if (p.status !== 'announced') return null;
    const w = saleWindow(p);
    if (w === null) return 'announced';
    const now = Number.isFinite(nowMs) ? nowMs : Date.now();
    if (now < w[0]) return 'announced';
    return now < w[1] ? 'live' : 'ended';
  }

  // ── the bar ──────────────────────────────────────────────────────────────

  function esc(t) {
    return String(t == null ? '' : t).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }

  function say(key, en) {
    try {
      const i18n = (typeof window !== 'undefined') ? window.RCI18N : null;
      const v = i18n ? i18n.translate(key, i18n.getLang()) : '';
      return v || en;
    } catch (e) {
      return en;
    }
  }

  /** Whether this visitor closed the bar for the sale that closes at `closesAt`. Unreadable storage: not closed. */
  function dismissed(storage, closesAt) {
    try {
      return !!storage && storage.getItem(DISMISS_KEY) === closesAt;
    } catch (e) {
      return false;
    }
  }

  function remember(storage, closesAt) {
    try {
      if (storage) storage.setItem(DISMISS_KEY, closesAt);
    } catch (e) { /* a private window: the bar closes for this page only */ }
  }

  /**
   * The bar's markup for a live presale. The closing time is its own chip, in digits and UTC, so
   * the translated sentence carries no date; the i18n keys sit on their elements, so a language
   * switch translates the bar in place.
   */
  function barHtml(presale) {
    const closes = windowText(presale.closes_at);
    return '<span class="pb-dot" aria-hidden="true"></span>'
      + '<span class="pb-text" data-i18n="home.token_presale_live">' + esc(say('home.token_presale_live', BAR_TEXT)) + '</span>'
      + '<span class="pb-time" title="' + esc(say('bar.presale_closes', BAR_CLOSES)) + '">'
      + '<span class="pb-closes" data-i18n="bar.presale_closes">' + esc(say('bar.presale_closes', BAR_CLOSES)) + '</span> '
      + '<time datetime="' + esc(presale.closes_at) + '">' + esc(closes) + '</time></span>'
      + '<a class="pb-cta" href="/token" data-i18n="bar.presale_cta">' + esc(say('bar.presale_cta', BAR_CTA)) + '</a>'
      + '<button type="button" class="pb-close" aria-label="' + esc(say('dd.dc_close', BAR_CLOSE))
      + '" data-i18n-attr="aria-label:dd.dc_close">✕</button>';
  }

  const CSS = '.presale-bar{display:flex;align-items:center;flex-wrap:wrap;gap:6px 12px;padding:8px 44px 8px 16px;'
    + 'position:relative;z-index:60;background:var(--up-dim,#0f2a1c);color:var(--text,#e6edf3);'
    + 'border-bottom:1px solid var(--up,#3fb950);font-size:14px;line-height:1.4}'
    + '.presale-bar .pb-dot{width:8px;height:8px;border-radius:50%;background:var(--up,#3fb950);flex:none}'
    + '.presale-bar .pb-text{font-weight:600}'
    + '.presale-bar .pb-time{color:var(--text-2,#9aa4ae);font-variant-numeric:tabular-nums}'
    + '.presale-bar .pb-cta{color:var(--up,#3fb950);font-weight:600;text-decoration:none}'
    + '.presale-bar .pb-cta:hover,.presale-bar .pb-cta:focus-visible{text-decoration:underline}'
    + '.presale-bar .pb-close{position:absolute;right:8px;top:50%;transform:translateY(-50%);background:none;'
    + 'border:0;color:inherit;font-size:16px;line-height:1;padding:8px;cursor:pointer;opacity:.75}'
    + '.presale-bar .pb-close:hover,.presale-bar .pb-close:focus-visible{opacity:1}';

  /**
   * Put the bar on the page when the record says the sale is live, and nowhere else. Returns the
   * element, or null with nothing changed.
   */
  function showBar(doc, token, nowMs, storage) {
    const p = token && token.presale;
    if (presalePhase(p, nowMs) !== 'live') return null;
    if (!doc || !doc.body || doc.getElementById(BAR_ID) || doc.getElementById('tok-root')) return null;
    if (dismissed(storage, p.closes_at)) return null;
    if (!doc.getElementById(BAR_ID + '-css')) {
      const style = doc.createElement('style');
      style.id = BAR_ID + '-css';
      style.textContent = CSS;
      (doc.head || doc.body).appendChild(style);
    }
    const bar = doc.createElement('div');
    bar.id = BAR_ID;
    bar.className = 'presale-bar';
    bar.setAttribute('role', 'region');
    bar.setAttribute('aria-label', say('home.token_presale_live', BAR_TEXT));
    bar.innerHTML = barHtml(p);
    const close = bar.querySelector('.pb-close');
    if (close) {
      close.addEventListener('click', function () {
        remember(storage, p.closes_at);
        if (bar.parentNode) bar.parentNode.removeChild(bar);
      });
    }
    doc.body.insertBefore(bar, doc.body.firstChild);
    return bar;
  }

  /** Resolves once the page has a body: this script may load in <head>. */
  function bodyReady(doc) {
    if (doc.readyState !== 'loading' || !doc.addEventListener) return Promise.resolve();
    return new Promise(function (resolve) { doc.addEventListener('DOMContentLoaded', resolve, { once: true }); });
  }

  function bootBar(doc, storage) {
    return fetch('/api/token', { headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(10000) })
      .then(function (r) { return r.status === 200 ? r.json() : null; })
      .then(function (body) {
        if (!body || typeof body !== 'object') return null;
        return bodyReady(doc).then(function () { return showBar(doc, body.token, undefined, storage); });
      })
      .catch(function () { return null; });
  }

  if (typeof document !== 'undefined' && document.getElementById && typeof fetch === 'function') {
    let storage = null;
    try { storage = window.localStorage; } catch (e) { storage = null; }
    bootBar(document, storage);
  }

  return {
    WINDOW_AT, windowMs, windowText, saleWindow, presalePhase,
    BAR_ID, DISMISS_KEY, BAR_TEXT, BAR_CTA, BAR_CLOSE, BAR_CLOSES, barHtml, showBar, bootBar, dismissed,
  };
}));
