/**
 * The per-signal page: the thesis and its counter-case.
 *
 * The route has already split the stored reasoning. This file only says
 * what that reading was. A missing field is not a thesis, an empty string
 * is not a thesis, and a record that did not arrive is not "no thesis".
 *
 * Dual export: browser (window.SignalPage) + Node (require).
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.SignalPage = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // The decision log's sentence for a reasoning column that holds nothing.
  const NO_THESIS = 'no thesis on record';
  const NO_COUNTER = 'No counter-case on record.';
  const UNAVAILABLE = 'This signal could not be read. The record is unavailable.';
  const NOT_FOUND = 'No signal with that id is on record.';
  const NO_KEY = 'This link has no signal id.';

  function esc(t) {
    return String(t == null ? '' : t).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }

  function say(key, en) {
    if (!key) return en;
    try {
      const i18n = (typeof window !== 'undefined') ? window.RCI18N : null;
      const v = i18n ? i18n.translate(key, i18n.getLang()) : '';
      return v || en;
    } catch (e) {
      return en;
    }
  }

  /**
   * The page URL. Query, never a path segment: BTC/USDT must survive the hop.
   * null when there is no id to open.
   */
  function href(key) {
    if (key == null) return null;
    const k = String(key);
    if (!k.trim()) return null;
    return '/signal?key=' + encodeURIComponent(k);
  }

  function apiHref(key) {
    const page = href(key);
    return page ? ('/api' + page) : null;
  }

  /**
   * The stream's link, or '' when the row has no id. The label is escaped.
   * A row with no id does not invent a URL from the symbol.
   */
  function streamLink(signal, escFn, label) {
    if (!signal || typeof signal !== 'object') return '';
    if (!Object.prototype.hasOwnProperty.call(signal, 'signal_key')) return '';
    const url = href(signal.signal_key);
    if (!url) return '';
    const safe = typeof escFn === 'function' ? escFn : esc;
    return ' · <a href="' + safe(url) + '">' + safe(label == null ? '' : label) + '</a>';
  }

  /**
   * Where the id is. Query wins. A path segment is accepted when the query
   * did not carry one. A percent-encoding that does not decode is unreadable,
   * which is not the same as a missing id.
   */
  function keyFrom(search, pathname) {
    let params;
    try {
      params = new URLSearchParams(search == null ? '' : String(search));
    } catch (e) {
      return { unreadable: true };
    }
    if (params.has('key')) {
      const q = params.get('key');
      if (q == null) return { unreadable: true };
      if (!String(q).trim()) return { missing: true };
      return { key: String(q) };
    }
    const path = String(pathname || '');
    const m = /^\/signal\/([^/?#]+)$/.exec(path);
    if (!m) return { missing: true };
    let decoded;
    try { decoded = decodeURIComponent(m[1]); }
    catch (e) { return { unreadable: true }; }
    if (!String(decoded).trim()) return { missing: true };
    return { key: decoded };
  }

  function fault(kind) {
    const text = kind === 'not_found' ? NOT_FOUND
      : kind === 'no_key' ? NO_KEY
      : UNAVAILABLE;
    return '<p class="sig-absent" role="status">' + esc(text) + '</p>';
  }

  /** recorded / absent / unreadable. Empty and non-strings are absent. */
  function fieldState(payload, name) {
    if (!payload || typeof payload !== 'object'
        || !Object.prototype.hasOwnProperty.call(payload, name)) {
      return 'unreadable';
    }
    const v = payload[name];
    if (typeof v !== 'string' || !v.trim()) return 'absent';
    return 'recorded';
  }

  function identity(payload) {
    const parts = [];
    if (typeof payload.direction === 'string' && payload.direction.trim()) {
      parts.push(payload.direction.trim());
    }
    if (typeof payload.symbol === 'string' && payload.symbol.trim()) {
      parts.push(payload.symbol.trim());
    }
    if (!parts.length) return '';
    return '<p class="sig-id">' + esc(parts.join(' · ')) + '</p>';
  }

  function section(headingKey, headingEn, state, text, absentKey, absentEn) {
    const head = '<h2>' + esc(say(headingKey, headingEn)) + '</h2>';
    if (state === 'recorded') {
      return head + '<p class="sig-prose">' + esc(text) + '</p>';
    }
    return head + '<p class="sig-absent">' + esc(say(absentKey, absentEn)) + '</p>';
  }

  /**
   * The page body for a payload that arrived. A payload that does not carry
   * both readings is unreadable — it is not "no thesis".
   */
  function render(payload) {
    const thesisState = fieldState(payload, 'thesis');
    const counterState = fieldState(payload, 'counter_case');
    if (thesisState === 'unreadable' || counterState === 'unreadable') return fault('unavailable');
    return identity(payload)
      + section('dd.dc_t_thesis', 'Thesis', thesisState, payload.thesis,
        'dd.dl_no_thesis', NO_THESIS)
      + section('', 'Counter-case', counterState, payload.counter_case,
        '', NO_COUNTER);
  }

  function boot(el) {
    const loc = (typeof location !== 'undefined') ? location : { search: '', pathname: '' };
    const found = keyFrom(loc.search, loc.pathname);
    if (found.unreadable) { el.innerHTML = fault('unavailable'); return; }
    if (!found.key) { el.innerHTML = fault('no_key'); return; }
    const url = apiHref(found.key);
    fetch(url, { headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(10000) })
      .then(function (r) {
        return r.json().then(function (body) {
          return { status: r.status, body: body };
        }, function () {
          return { status: r.status, body: null };
        });
      })
      .then(function (res) {
        if (res.status === 404) { el.innerHTML = fault('not_found'); return; }
        if (res.status !== 200) { el.innerHTML = fault('unavailable'); return; }
        el.innerHTML = render(res.body);
      })
      .catch(function () { el.innerHTML = fault('unavailable'); });
  }

  if (typeof document !== 'undefined' && document.getElementById) {
    const el = document.getElementById('sig-root');
    if (el) boot(el);
  }

  return {
    href, apiHref, streamLink, keyFrom, fieldState, render, fault, boot,
    NO_THESIS, NO_COUNTER, UNAVAILABLE, NOT_FOUND, NO_KEY,
  };
}));
