/**
 * RUNECLAW shared runtime — session, fetch, panel states, toasts, SSE,
 * formatters. Loaded by every page before page-specific scripts.
 * No framework, no build step: everything hangs off `window.RC`.
 */
(function () {
  'use strict';

  // ── Session ────────────────────────────────────────────────────────────
  //
  // M14: the session lives in an HttpOnly cookie the page cannot read. What
  // remains readable is `rc_auth=1`, a flag carrying no secret, because
  // LOGGED_IN drives the entire UI — which nav renders, whether a panel shows
  // a login gate, whether the dashboard boots — and deriving it from "can I
  // see a token" stopped being answerable the moment the token became
  // unreadable. Forging the flag buys a UI shell whose every request 401s.
  //
  // resolveToken() still reads localStorage, for exactly one purpose: users
  // who logged in before this shipped have a token there and no cookie, and
  // migrate() below trades it for one. Nobody is logged out by the change.
  function resolveToken() {
    const legacy = localStorage.getItem('token');
    if (legacy) return legacy;
    try {
      const s = JSON.parse(localStorage.getItem('rc_session') || 'null');
      return (s && s.token) || null;
    } catch (e) { return null; }
  }
  function hasSessionCookie() {
    return /(^|;\s*)rc_auth=1(\s*;|\s*$)/.test(document.cookie || '');
  }
  function forgetStoredToken() {
    localStorage.removeItem('token');
    localStorage.removeItem('rc_session');
  }
  const TOKEN = resolveToken();
  const LOGGED_IN = !!TOKEN || hasSessionCookie();

  /**
   * One-time upgrade for a session that predates the cookie.
   *
   * GET /auth/me runs through the same funnel every login does, so it answers
   * with a Set-Cookie; after that the stored copy is redundant and is deleted.
   * Deleted only on a 200 — clearing it on a network blip would log the user
   * out to fix a security property they already had.
   */
  async function migrateStoredToken() {
    if (!TOKEN || hasSessionCookie()) return;
    try {
      const r = await fetch('/api/auth/me', {
        headers: { Authorization: 'Bearer ' + TOKEN },
        credentials: 'same-origin',
        signal: AbortSignal.timeout(8000),
      });
      if (r.ok && hasSessionCookie()) forgetStoredToken();
    } catch (e) { /* try again next page load; nothing is lost by waiting */ }
  }

  // A rejected promise nobody caught is silent in a browser. Every panel
  // loader is async, so a throw inside one — a TypeError on an unexpected
  // payload shape, say — leaves no trace at all: no console entry, no
  // window.onerror (that only fires for synchronous throws), nothing. The
  // page just quietly renders less than it should, and the failure is
  // invisible to whoever is trying to reproduce it.
  //
  // Same asymmetry the server settled on: name it loudly, change nothing.
  // Pages that show a visible reporter listen for this event as well.
  window.addEventListener('unhandledrejection', (e) => {
    const r = e && e.reason;
    // eslint-disable-next-line no-console
    console.error('UNHANDLED REJECTION (page kept running):',
      (r && (r.stack || r.message)) || r);
  });

  function authHeaders() {
    return TOKEN ? { Authorization: 'Bearer ' + TOKEN } : {};
  }
  function logout() {
    // The cookie is HttpOnly, so the page CANNOT delete it — only the server
    // can, and only the server can bump the token epoch that actually ends the
    // session. Clearing localStorage and redirecting, as this used to do, now
    // leaves a live session behind on the very action whose entire purpose is
    // ending one. Navigate on either outcome: a logout that appears to hang
    // because the network is down is worse than one that redirects and leaves
    // the epoch bump for the next attempt.
    forgetStoredToken();
    fetch('/api/auth/logout', {
      method: 'POST',
      headers: TOKEN ? { Authorization: 'Bearer ' + TOKEN } : {},
      credentials: 'same-origin',
      signal: AbortSignal.timeout(5000),
    }).catch(() => {}).then(() => { location.href = '/'; });
  }

  // ── fetchJSON: timeout + auth + typed errors ───────────────────────────
  async function fetchJSON(url, { method = 'GET', body, timeoutMs = 10000, auth = true, signal } = {}) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    // Optional caller-supplied signal (e.g. a Cancel button): abort the request
    // when it fires, on top of the timeout.
    if (signal) {
      if (signal.aborted) ctrl.abort();
      else signal.addEventListener('abort', () => ctrl.abort(), { once: true });
    }
    try {
      const r = await fetch(url, {
        method,
        signal: ctrl.signal,
        // Explicit rather than relying on the same-origin default: the session
        // now travels as a cookie, and a default is a thing that changes.
        credentials: 'same-origin',
        headers: {
          ...(auth ? authHeaders() : {}),
          ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
      let data = null;
      try { data = await r.json(); } catch (e) { /* non-JSON body */ }
      return { ok: r.ok, status: r.status, data };
    } finally {
      clearTimeout(timer);
    }
  }

  // ── postWithStepUp: 2FA step-up on money-moving actions ────────────────
  // POST `url`; if the server demands a fresh authenticator code (HTTP 401
  // { error:'two_factor_required' } — a live-money action on a 2FA-enrolled
  // account), prompt once and retry with the code appended. A cancelled
  // prompt surfaces the original 401 so the caller can show the message.
  // Non-2FA accounts and paper actions never trigger the prompt (the server
  // only returns that status when it genuinely needs the step-up).
  async function postWithStepUp(url, body, opts = {}) {
    let r = await fetchJSON(url, { method: 'POST', body, ...opts })
      .catch(() => ({ ok: false, data: null }));
    if (r && r.status === 401 && r.data && r.data.error === 'two_factor_required') {
      const code = (window.prompt(
        r.data.detail || 'Enter your 6-digit authenticator code:') || '').trim();
      if (!code) return r;
      r = await fetchJSON(url, { method: 'POST', body: { ...body, totp_code: code }, ...opts })
        .catch(() => ({ ok: false, data: null }));
    }
    return r;
  }

  // ── Formatters ──────────────────────────────────────────────────────────
  function esc(s) {
    return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function fmt(n, d = 2) { return n != null && isFinite(n) ? Number(n).toFixed(d) : '--'; }
  function fmtMoney(n, d = 2) {
    if (n == null || !isFinite(n)) return '--';
    return '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  }
  function fmtPrice(n) {
    if (n == null || !isFinite(n)) return '--';
    const v = Number(n);
    const d = v >= 1000 ? 2 : v >= 1 ? 4 : 6;
    return '$' + v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: d });
  }
  function fmtK(n) {
    if (n == null || !isFinite(n)) return '--';
    n = Number(n);
    if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
    return n.toFixed(0);
  }
  function signed(n, d = 2) {
    if (n == null || !isFinite(n)) return '--';
    return (Number(n) >= 0 ? '+' : '') + Number(n).toFixed(d);
  }
  function pnlClass(n) {
    const v = Number(n);
    if (n == null || !isFinite(v)) return '';  // unknown -> muted, never red
    return v >= 0 ? 'pos' : 'neg';
  }
  function fmtAgo(iso) {
    if (!iso) return '--';
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (!isFinite(s)) return '--';
    if (s < 60) return 'just now';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }
  // Direction chip: glyph + text, never color alone.
  function dirChip(direction) {
    const up = String(direction).toUpperCase() === 'LONG' || String(direction).toUpperCase() === 'BUY';
    return `<span class="chip ${up ? 'chip--up' : 'chip--down'}">${up ? '▲ LONG' : '▼ SHORT'}</span>`;
  }

  // ── Bot-HTML sanitizer (whitelist: b, i, code, pre, br) ─────────────────
  function sanitizeBotHtml(html) {
    const ALLOWED = { B: 'b', I: 'i', CODE: 'code', PRE: 'pre' };
    let doc;
    try { doc = new DOMParser().parseFromString(`<div>${html}</div>`, 'text/html'); }
    catch (e) { return esc(html); }
    const walk = (node) => {
      let out = '';
      node.childNodes.forEach(ch => {
        if (ch.nodeType === Node.TEXT_NODE) out += esc(ch.textContent);
        else if (ch.nodeType === Node.ELEMENT_NODE) {
          if (ch.tagName === 'BR') out += '<br>';
          else if (ALLOWED[ch.tagName]) out += `<${ALLOWED[ch.tagName]}>${walk(ch)}</${ALLOWED[ch.tagName]}>`;
          else out += walk(ch);
        }
      });
      return out;
    };
    return walk(doc.body.firstChild || doc.body);
  }

  // ── Toasts (aria-live region) ────────────────────────────────────────────
  function toastRegion() {
    let el = document.getElementById('toastRegion');
    if (!el) {
      el = document.createElement('div');
      el.id = 'toastRegion';
      el.className = 'toast-region';
      el.setAttribute('aria-live', 'polite');
      document.body.appendChild(el);
    }
    return el;
  }
  function toast(message, kind = '') {
    const region = toastRegion();
    const t = document.createElement('div');
    t.className = 'toast' + (kind ? ` toast--${kind}` : '');
    t.textContent = message;
    region.appendChild(t);
    setTimeout(() => t.remove(), 5000);
    while (region.children.length > 4) region.firstChild.remove();
  }

  // ── renderPanel: the ONE loading/empty/error pattern ─────────────────────
  // renderPanel(el, loader, opts)
  //   loader: async () => html string | '' (empty) | null (empty)
  //   opts: { timeoutMs, empty: {icon, text, cta: {label, href|onClick}}, errorText }
  // States: skeleton -> data | empty (in-app CTA) | error (Retry button).
  // Resolved per render, never cached at module load — the language switcher
  // changes the answer after boot.
  function t(key, en) {
    try { return (window.RCI18N && window.RCI18N.translate(key, window.RCI18N.getLang())) || en; }
    catch (e) { return en; }
  }
  // A non-2xx answer is a failure to READ, never proof of absence. Rendering a
  // server error as "nothing here yet" tells the user a lie about their own
  // money — the panel must say it couldn't look, and offer Retry. Throwing is
  // how a loader says that: renderPanel's catch turns it into the error state.
  // 404 is the one honest exception (letter-for-that-week, a deleted record):
  // the resource genuinely isn't there, so it falls through as empty.
  function mustRead(r) {
    if (!r || (!r.ok && r.status !== 404)) {
      const e = new Error('panel read failed: HTTP ' + (r ? r.status : 'no response'));
      // Carried so the panel can tell "try again" apart from "you are signed
      // out" — a Retry button on an expired session retries forever.
      e.status = r ? r.status : 0;
      throw e;
    }
    return r.ok ? r.data : null;
  }
  function stateBlock({ icon = 'icon-inbox', text = null, cta = null }) {
    if (text == null) text = t('dd.nothing_here', 'Nothing here yet.');
    return `<div class="state-block">
      <svg class="icon"><use href="#${icon}"></use></svg>
      <p>${esc(text)}</p>
      ${cta ? `<a class="btn btn--sm" href="${esc(cta.href || '#')}">${esc(cta.label)}</a>` : ''}
    </div>`;
  }
  async function renderPanel(el, loader, opts = {}) {
    if (!el) return;
    const { timeoutMs = 8000, empty = {} } = opts;
    // 91 of 95 panels pass no errorText, so this default IS the dashboard's
    // failure voice — it has to speak the user's language.
    const errorText = opts.errorText || t('dd.err_panel', "Couldn't load this panel.");
    // Refresh-in-place: once a panel has shown REAL content, periodic
    // re-renders (the every() timers, SSE nudges) keep it on screen while
    // the loader runs — no skeleton flash every 15-30s, and a transient
    // fetch failure keeps last-known data instead of blanking to an error.
    const hasContent = el.dataset.rcLoaded === '1';
    if (!hasContent) {
      el.innerHTML = '<div class="skel"></div><div class="skel"></div><div class="skel"></div>';
    }
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; fail(); }, timeoutMs);
    function fail(err) {
      if (hasContent) return;                 // stale beats blank
      // An expired session is a different answer from a failed read, and it
      // needs a different action: Retry would loop forever against a 401.
      const expired = !!(err && err.status === 401);
      const text = expired ? t('dd.session_expired', 'Your session expired — sign in again to see this.')
        : errorText;
      // data-i18n on the default copy so the failure state re-translates when
      // the user switches language, and self-heals if a panel failed before
      // i18n.js finished loading. A caller-supplied errorText is already
      // resolved by its own T() and carries no key.
      const key = expired ? 'dd.session_expired' : (opts.errorText ? null : 'dd.err_panel');
      const action = expired
        ? `<a class="btn btn--sm" href="/" data-i18n="dd.sign_in">${esc(t('dd.sign_in', 'Sign in'))}</a>`
        : `<button class="btn btn--sm" type="button" data-i18n="dd.retry">${esc(t('dd.retry', 'Retry'))}</button>`;
      el.innerHTML = `<div class="state-block">
        <svg class="icon"><use href="#icon-offline"></use></svg>
        <p${key ? ` data-i18n="${key}"` : ''}>${esc(text)}</p>
        ${action}
      </div>`;
      if (window.RCI18N) RCI18N.apply(el);
      const btn = el.querySelector('button');
      if (btn) btn.onclick = () => renderPanel(el, loader, opts);
    }
    try {
      const html = await loader();
      clearTimeout(timer);
      if (timedOut && hasContent) return;
      if (html == null || html === '') {
        delete el.dataset.rcLoaded;
        el.innerHTML = stateBlock(empty);
        if (window.RCI18N) RCI18N.apply(el);
      } else {
        // Count-up runs on FIRST population only — soft SSE refreshes keep the
        // live-value flash, they don't re-roll the number.
        const wasLoaded = el.dataset.rcLoaded === '1';
        el.innerHTML = html;
        el.dataset.rcLoaded = '1';
        // Async content translates like static markup: any data-i18n the
        // loader emitted is applied the moment it lands.
        if (window.RCI18N) RCI18N.apply(el);
        if (!wasLoaded) animateCounters(el);
      }
    } catch (e) {
      clearTimeout(timer);
      // A swallowed exception makes a genuine loader bug (TypeError, bad shape)
      // indistinguishable from a dead endpoint. Name it in the console; the
      // user still just sees the calm error state.
      console.warn('panel failed:', (e && (e.stack || e.message)) || e);
      if (!timedOut) fail(e);
    }
  }

  // ── SSE (server push -> named callbacks) ─────────────────────────────────
  //
  // EventSource auto-reconnects, but ONLY after a transport-level drop. Per
  // spec, a response whose status is not 200 — or whose content-type is not
  // text/event-stream — makes the browser "fail the connection": it fires
  // error, sets readyState to CLOSED, and never tries again.
  //
  // This app produces exactly that response. The not-ready gate in server.js
  // refuses every /api/* with a 503 JSON body while the schema is unmigrated,
  // and an expired token gets a 401. So a database blip — of which there were
  // several today — permanently killed the stream in every open tab. The page
  // stayed rendered, the panels stayed populated, and nothing updated again
  // until someone reloaded. "It works and after a little time it doesn't,
  // seems like it disconnects" is precisely this.
  //
  // The old handler was a comment asserting the opposite:
  //     es.onerror = () => { /* browser auto-reconnects; polling covers gaps */ };
  // Both halves were wrong for the failure this app actually generates.
  //
  // So: reconnect ourselves when the browser has given up, with capped
  // exponential backoff plus jitter — the server that 503s is a server under
  // strain, and every open tab retrying in lockstep is how a recovering
  // process gets knocked back down.
  const STREAM_RETRY_MIN_MS = 2000;
  const STREAM_RETRY_MAX_MS = 60000;

  function connectStream(handlers, opts = {}) {
    if (typeof EventSource === 'undefined') return null;
    const onState = typeof (opts && opts.onState) === 'function' ? opts.onState : () => {};
    let es = null, timer = null, delay = STREAM_RETRY_MIN_MS, attempts = 0, closed = false;

    function schedule() {
      if (closed || timer) return;
      attempts += 1;
      // Jitter ±25% so N tabs do not retry on the same tick.
      const wait = Math.round(delay * (0.75 + Math.random() * 0.5));
      onState({ connected: false, attempts, retryInMs: wait });
      timer = setTimeout(() => { timer = null; open(); }, wait);
      delay = Math.min(delay * 2, STREAM_RETRY_MAX_MS);
    }

    function open() {
      if (closed) return;
      try { es = new EventSource('/api/stream'); }
      catch (e) { es = null; schedule(); return; }
      es.onopen = () => {
        delay = STREAM_RETRY_MIN_MS; attempts = 0;
        onState({ connected: true, attempts: 0 });
      };
      Object.entries(handlers || {}).forEach(([evt, fn]) => es.addEventListener(evt, fn));
      es.onerror = () => {
        // CONNECTING means the browser is retrying on its own — leave it be,
        // or we would race it and open two streams. CLOSED means it gave up,
        // and it gives up permanently.
        if (es && es.readyState === 2 /* CLOSED */) {
          try { es.close(); } catch (e) { /* already gone */ }
          es = null;
          schedule();
        } else {
          onState({ connected: false, attempts });
        }
      };
    }

    open();
    return {
      close() {
        closed = true;
        if (timer) { clearTimeout(timer); timer = null; }
        if (es) { try { es.close(); } catch (e) { /* already gone */ } es = null; }
      },
      get readyState() { return es ? es.readyState : 2; },
    };
  }

  // ── Modal a11y: focus-trap + inert background + focus-return ─────────────
  // Turns a role="dialog" element (a direct <body> child) into a proper modal.
  // Call open() when you show it and close() when you hide it. Handles WCAG
  // 2.4.3 (focus order) + 4.1.2: sets aria-modal, makes every OTHER top-level
  // element `inert` (SR + Tab can't reach the page behind it), cycles Tab /
  // Shift+Tab inside the dialog, and returns focus to whatever was focused when
  // it opened (the trigger). `inert` degrades gracefully where unsupported —
  // the keydown trap still keeps Tab inside.
  function modalA11y(dialog) {
    let prevFocus = null;
    const focusables = () => Array.from(dialog.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'))
      .filter(el => !el.disabled && el.getClientRects().length > 0);
    function onKeydown(e) {
      if (e.key !== 'Tab') return;
      const f = focusables();
      if (!f.length) { e.preventDefault(); return; }
      const first = f[0], last = f[f.length - 1], a = document.activeElement;
      if (e.shiftKey && (a === first || !dialog.contains(a))) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (a === last || !dialog.contains(a))) { e.preventDefault(); first.focus(); }
    }
    function setInert(on) {
      Array.from(document.body.children).forEach(el => {
        if (el === dialog || el.tagName === 'SCRIPT') return;
        if (on) el.setAttribute('inert', '');
        else el.removeAttribute('inert');
      });
    }
    return {
      open(focusEl) {
        prevFocus = document.activeElement;
        dialog.setAttribute('aria-modal', 'true');
        setInert(true);
        document.addEventListener('keydown', onKeydown, true);
        const target = focusEl || focusables()[0] || dialog;
        if (target && target.focus) { try { target.focus(); } catch (e) { /* noop */ } }
      },
      close() {
        document.removeEventListener('keydown', onKeydown, true);
        setInert(false);
        dialog.removeAttribute('aria-modal');
        if (prevFocus && prevFocus.focus) { try { prevFocus.focus(); } catch (e) { /* noop */ } }
        prevFocus = null;
      },
    };
  }

  // ── Count-up: roll a number to its value on first appearance ─────────────
  // Premium feel without lying — the FINAL value is exact; only the approach is
  // animated. Reduced-motion (or dur<=0) sets the value instantly.
  const _reducedMotion = () =>
    !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);

  function countUp(el, to, opts = {}) {
    const { from = 0, dur = 850, decimals = 0, prefix = '', suffix = '' } = opts;
    to = Number(to);
    if (!el || !isFinite(to)) return;
    const fmtN = (v) => prefix + Number(v).toLocaleString(undefined,
      { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) + suffix;
    if (_reducedMotion() || dur <= 0 || !window.requestAnimationFrame) {
      el.textContent = fmtN(to); return;
    }
    el.classList.add('rc-counting');
    const t0 = performance.now();
    const tick = (t) => {
      const p = Math.min(1, (t - t0) / dur);
      const eased = 1 - Math.pow(1 - p, 3);   // easeOutCubic
      el.textContent = fmtN(from + (to - from) * eased);
      if (p < 1) requestAnimationFrame(tick); else el.textContent = fmtN(to);
    };
    requestAnimationFrame(tick);
  }

  // Roll up any [data-count] elements inside a freshly-rendered subtree. Opt-in
  // per element: data-count="1234.5" [data-count-dec] [data-count-prefix]
  // [data-count-suffix]. Each element animates once.
  function animateCounters(root) {
    if (!root || !root.querySelectorAll) return;
    root.querySelectorAll('[data-count]').forEach((el) => {
      if (el.dataset.rcCounted) return;
      el.dataset.rcCounted = '1';
      countUp(el, el.dataset.count, {
        decimals: parseInt(el.dataset.countDec || '0', 10) || 0,
        prefix: el.dataset.countPrefix || '',
        suffix: el.dataset.countSuffix || '',
      });
    });
  }

  // Scroll-reveal: ease .reveal-on-scroll sections up as they enter the
  // viewport, once each. Reduced-motion (or a browser without
  // IntersectionObserver) reveals everything immediately so content is never
  // stranded invisible. Safe to call repeatedly — already-revealed and
  // already-observed elements are skipped.
  let _revealObserver = null;
  function revealOnScroll(root = document) {
    if (!root || !root.querySelectorAll) return;
    const els = root.querySelectorAll('.reveal-on-scroll:not(.rc-inview)');
    if (!els.length) return;
    if (_reducedMotion() || !('IntersectionObserver' in window)) {
      els.forEach((el) => el.classList.add('rc-inview'));
      return;
    }
    if (!_revealObserver) {
      _revealObserver = new IntersectionObserver((entries, obs) => {
        entries.forEach((e) => {
          if (e.isIntersecting) { e.target.classList.add('rc-inview'); obs.unobserve(e.target); }
        });
      }, { rootMargin: '0px 0px -8% 0px', threshold: 0.08 });
    }
    els.forEach((el) => {
      if (el.dataset.rcReveal) return;
      el.dataset.rcReveal = '1';
      _revealObserver.observe(el);
    });
  }
  // Wire any sections present at load; views injected later re-arm via RC.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => revealOnScroll());
  } else {
    revealOnScroll();
  }

  // ── Page index: an entry is a claim that the section is there ───────────
  //
  // The landing page's index lists every section. Two of those sections
  // (#theaterSection, #boardTease) ship `hidden` and are revealed only once
  // real data loads — the same "real or nothing" rule the sealed-call widget
  // follows, because a live-trade panel with nothing in it is worse than no
  // panel.
  //
  // An index entry pointing at one of them before it appears is that defect
  // moved into the navigation: a contents line asserts the content exists, and
  // a visitor who clicks it and lands nowhere has been misled by the site's
  // own map. So entries start hidden when their target is absent or hidden,
  // and appear when the section does.
  //
  // OBSERVED, NOT POLLED, and not ordered: theater.js and the board loader
  // each reveal their section whenever their own fetch resolves. Anything that
  // assumed an order here would work on a fast connection and silently leave
  // an entry hidden on a slow one — a wrong answer that only appears for the
  // people already having the worst time.
  function syncPageIndex() {
    const nav = document.getElementById('pageIndex');
    if (!nav) return;                      // every other page: nothing to do
    const watched = [];
    nav.querySelectorAll('a[href^="#"]').forEach((a) => {
      const li = a.closest('li') || a;
      const target = document.getElementById(a.getAttribute('href').slice(1));
      if (!target) { li.hidden = true; return; }   // section gone entirely
      const apply = () => { li.hidden = target.hidden; };
      apply();
      if (target.hidden) watched.push([target, apply]);
    });
    if (!watched.length || typeof MutationObserver !== 'function') return;
    const obs = new MutationObserver((records) => {
      records.forEach((r) => {
        const hit = watched.find(([el]) => el === r.target);
        if (hit) hit[1]();
      });
      if (watched.every(([el]) => !el.hidden)) obs.disconnect();
    });
    watched.forEach(([el]) => obs.observe(el, { attributes: true, attributeFilter: ['hidden'] }));
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', syncPageIndex);
  } else {
    syncPageIndex();
  }

  // Trade a pre-cookie localStorage session for a cookie, once, in the
  // background. Deliberately not awaited: it must never delay first paint, and
  // every request on this page still authenticates by header meanwhile.
  migrateStoredToken();

  window.RC = {
    TOKEN, LOGGED_IN, authHeaders, logout, hasSessionCookie, forgetStoredToken,
    fetchJSON, postWithStepUp, esc, fmt, fmtMoney, fmtPrice, fmtK, signed, pnlClass, fmtAgo,
    dirChip, sanitizeBotHtml, toast, renderPanel, stateBlock, mustRead, connectStream,
    modalA11y, countUp, animateCounters, revealOnScroll, syncPageIndex,
  };
})();
