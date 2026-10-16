/*
 * The operator's Admin page (app/public/admin.html).
 *
 * One read, /api/admin/settings, carries two sources: the bot's settings
 * (`bot`) and this site's configuration findings (`web`). Each part is drawn
 * on its own, and a part that could not be read says so in its own panel,
 * so a bot outage does not blank the website half and nothing unread is shown
 * as an empty list.
 *
 * A change posts to the same route with RC.postWithStepUp, which asks for the
 * authenticator code when the server wants it. The answer comes from the bot:
 * the value before and after, whether it tightened or loosened the guard
 * (computed there from the setting's declared direction, never here), and
 * whether it was saved. The page then reads everything again.
 */
(function () {
  'use strict';
  const RC = window.RC;
  if (!RC) return;
  const esc = RC.esc;
  const $ = (id) => document.getElementById(id);

  const SOURCE = {
    default: 'the configured default',
    set: 'set by the operator and saved',
    moved: 'saved by the operator, and moved since (the adaptive threshold moves auto-confirm)',
    unsaved: 'changed since the bot started, and not saved',
    unknown: 'where it came from could not be read',
  };

  const BOT_ERROR = {
    telegram_required: 'Link your Telegram account to read the bot\'s settings here.',
    gateway_unconfigured: 'The bot gateway is not configured on this site, so the bot\'s settings cannot be read here.',
    operator_only: 'The bot does not recognise your linked Telegram account as an admin.',
    unreachable: 'The bot could not be reached.',
  };

  function shown(s, v) {
    if (v === null || v === undefined) {
      return (s.kind === 'int' || s.kind === 'number') ? 'no override' : 'not read';
    }
    if (s.kind === 'bool') return v ? 'on' : 'off';
    return String(v);
  }

  function editor(s, canChange) {
    const dis = canChange ? '' : ' disabled';
    let control;
    if (s.kind === 'bool') {
      control = `<select data-k="${esc(s.key)}"${dis}><option value="on">on</option><option value="off">off</option></select>`;
    } else if (s.kind === 'choice') {
      control = `<select data-k="${esc(s.key)}"${dis}>${(s.choices || []).map((c) =>
        `<option value="${esc(c)}"${c === s.value ? ' selected' : ''}>${esc(c)}</option>`).join('')}</select>`;
    } else {
      const step = s.kind === 'int' ? '1' : 'any';
      const lo = (s.lo === null || s.lo === undefined) ? '' : ` min="${esc(s.lo)}"`;
      const hi = (s.hi === null || s.hi === undefined) ? '' : ` max="${esc(s.hi)}"`;
      const val = (s.value === null || s.value === undefined) ? '' : ` value="${esc(s.value)}"`;
      control = `<input type="number" step="${step}"${lo}${hi}${val} data-k="${esc(s.key)}"${dis}>`;
    }
    const range = (s.lo !== null && s.lo !== undefined && s.hi !== null && s.hi !== undefined)
      ? ` <span class="muted">${esc(s.lo)}–${esc(s.hi)}</span>` : '';
    return `<div class="ed">
        <label>New value ${control}</label>${range}
        <button class="btn btn--sm" type="button" data-act="set" data-key="${esc(s.key)}"${dis}>Save</button>
        <button class="btn btn--sm" type="button" data-act="reset" data-key="${esc(s.key)}"${dis}>Reset to default</button>
      </div>`;
  }

  function settingCard(s, canChange) {
    const value = s.value_error
      ? `<span class="warn">could not be read (${esc(s.value_error)})</span>`
      : `<span class="val">${esc(shown(s, s.value))}</span>`;
    const eff = s.effective ? ` <span class="muted">· ${esc(s.effective)}</span>` : '';
    const src = SOURCE[s.source] || SOURCE.unknown;
    const def = (s.default === null || s.default === undefined)
      ? '' : ` Default: ${esc(shown(s, s.default))}.`;
    const door = s.door ? ` Telegram: <code>${esc(s.door)}</code>.` : '';
    return `<div class="set" data-card="${esc(s.key)}">
        <div class="hd"><span class="nm">${esc(s.label)}</span>${value}${eff}</div>
        <div class="src">Source: ${esc(src)}.${def}${door}</div>
        <div class="means">${esc(s.means)}</div>
        ${editor(s, canChange)}
        <div class="res" data-res="${esc(s.key)}"></div>
      </div>`;
  }

  function changeRows(rows, labels) {
    if (!rows.length) return '<div class="empty">No changes recorded yet.</div>';
    const body = rows.map((r) => {
      const at = r.at ? new Date(r.at) : null;
      const when = at && !isNaN(at.getTime()) ? at.toLocaleString() : 'time not recorded';
      const eff = r.effect === 'loosens' ? '🔓 loosens'
        : r.effect === 'tightens' ? '🔒 tightens'
          : r.effect === 'neither' ? 'not a guard'
            : r.effect === 'unknown' ? 'direction unknown' : 'no change';
      const saved = r.persisted === true ? 'saved' : r.persisted === false ? 'not saved' : 'not confirmed';
      const label = labels[r.key] || r.key;
      return `<tr><td class="mono" data-label="When">${esc(when)}</td><td data-label="Setting">${esc(label)}</td>
        <td class="mono" data-label="Change">${esc(r.reset ? 'reset' : '')} ${esc(JSON.stringify(r.before))} → ${esc(JSON.stringify(r.after))}</td>
        <td data-label="Effect">${esc(eff)}</td><td data-label="Who">${esc(r.via || '')} · ${esc(r.actor || '')}</td><td data-label="Saved">${esc(saved)}</td></tr>`;
    }).join('');
    return `<table class="rows"><thead><tr><th>When</th><th>Setting</th><th>Change</th><th>Effect</th><th>Who</th><th>Saved</th></tr></thead><tbody>${body}</tbody></table>`;
  }

  function flagRows(flags) {
    if (!flags.length) return '<div class="empty">No flags listed.</div>';
    const body = flags.map((f) => `<tr><td class="mono" data-label="Flag">${esc(f.name)}</td>
        <td class="mono" data-label="In force">${(f.value === null || f.value === undefined) ? '<span class="warn">not read</span>' : esc(f.value)}</td>
        <td data-label="What it does">${esc(f.means || '')}</td></tr>`).join('');
    return `<p class="note">Read once when the bot starts. Change them in the bot's .env and restart; the nightly self-audit proposes changes to these and never applies them.</p>
      <table class="rows"><thead><tr><th>Flag</th><th>In force</th><th>What it does</th></tr></thead><tbody>${body}</tbody></table>`;
  }

  function webRows(web) {
    const gw = web.gateway_configured ? 'configured' : '<span class="warn">not configured</span>';
    const findings = web.findings || [];
    const list = findings.length
      ? `<table class="rows"><thead><tr><th>Level</th><th>Setting</th><th>Finding</th></tr></thead><tbody>${findings.map((f) =>
        `<tr><td data-label="Level">${f.level === 'fatal' ? '<span class="warn">fatal</span>' : esc(f.level)}</td><td class="mono" data-label="Setting">${esc(f.key)}</td><td data-label="Finding">${esc(f.msg)}</td></tr>`).join('')}</tbody></table>`
      : '<div class="empty">No configuration findings.</div>';
    return `<p class="note">Bot gateway: ${gw}. These are the site's own environment settings; they are changed on the host and need a restart.</p>${list}`;
  }

  let lastRead = null;

  function paint(d) {
    lastRead = d;
    const canChange = Boolean(d.two_factor) && Boolean(d.bot);
    $('admState').innerHTML = d.two_factor
      ? ''
      : 'Changing settings from this page needs two-factor sign-in, which this account has not turned on. Reading works, and Telegram still works.';

    if (d.bot) {
      const b = d.bot;
      const settings = b.settings || [];
      const savedNote = b.saved_readable === false
        ? `<p class="warn">The saved settings file could not be read (${esc(b.saved_error || 'unknown')}). Values in force are shown; a change will not be saved until it can be read.</p>`
        : '';
      $('admSettings').innerHTML = savedNote + settings.map((s) => settingCard(s, canChange)).join('');
      $('admLocked').innerHTML = (b.locked || []).map((l) =>
        `<div class="set"><div class="hd"><span class="nm">${esc(l.label)}</span>
          <span class="val">${(l.value === null || l.value === undefined) ? '<span class="warn">not read</span>' : esc(l.value)}</span></div>
          <div class="means">${esc(l.why)}</div></div>`).join('');
      const labels = {};
      settings.forEach((s) => { labels[s.key] = s.label; });
      $('admChanges').innerHTML = Array.isArray(b.changes)
        ? changeRows(b.changes, labels)
        : `<div class="warn">The change record could not be read (${esc(b.changes_error || 'unknown')}).</div>`;
      $('admFlags').innerHTML = Array.isArray(b.flags)
        ? flagRows(b.flags)
        : `<div class="warn">The flags could not be read (${esc(b.flags_error || 'unknown')}).</div>`;
      document.querySelectorAll('select[data-k]').forEach((sel) => {
        const s = settings.find((x) => x.key === sel.dataset.k);
        if (s && s.kind === 'bool') sel.value = s.value ? 'on' : 'off';
      });
    } else {
      const why = BOT_ERROR[d.bot_error] || `The bot answered with an error (${esc(d.bot_error || 'unknown')}).`;
      ['admSettings', 'admLocked', 'admChanges', 'admFlags'].forEach((id) => {
        $(id).innerHTML = `<div class="warn">${esc(why)}</div>`;
      });
    }
    $('admWeb').innerHTML = d.web
      ? webRows(d.web)
      : `<div class="warn">The website's configuration could not be read (${esc(d.web_error || 'unknown')}).</div>`;
  }

  function refuse(text) {
    ['admSettings', 'admLocked', 'admChanges', 'admFlags', 'admWeb'].forEach((id) => { $(id).innerHTML = ''; });
    $('admState').innerHTML = `<div class="warn">${text}</div>`;
  }

  async function load() {
    const r = await RC.fetchJSON('/api/admin/settings', { timeoutMs: 20000 })
      .catch(() => ({ ok: false, status: 0, data: null }));
    if (r.status === 401) return refuse('Sign in as the operator to use this page. <a href="/">Sign in</a>');
    if (r.status === 403) return refuse('The Admin page is for the operator only.');
    if (!r.ok || r.unreadable || !r.data || typeof r.data !== 'object') {
      return refuse(`The Admin page could not be read${r.status ? ` (HTTP ${esc(r.status)})` : ''}. Try again shortly.`);
    }
    return paint(r.data);
  }

  async function change(key, reset) {
    const s = ((lastRead && lastRead.bot && lastRead.bot.settings) || []).find((x) => x.key === key);
    if (!s) return;
    const res = document.querySelector(`[data-res="${CSS.escape(key)}"]`);
    let body;
    if (reset) {
      body = { key, reset: true };
    } else {
      const el = document.querySelector(`[data-k="${CSS.escape(key)}"]`);
      const raw = el ? String(el.value).trim() : '';
      if (!raw) { if (res) res.textContent = 'Enter a value first.'; return; }
      body = { key, value: s.kind === 'bool' ? raw === 'on' : raw };
    }
    const to = reset ? 'its default' : shown(s, body.value);
    const ok = window.confirm(`${s.label}: ${shown(s, s.value)} → ${to}.\n\n${s.means}\n\nApply this change?`);
    if (!ok) return;
    if (res) res.textContent = 'Applying…';
    const r = await RC.postWithStepUp('/api/admin/settings', body, { timeoutMs: 20000 });
    const d = (r && r.data) || {};
    if (r && r.ok && d.ok) {
      const eff = d.effect === 'loosens' ? ' 🔓 This loosens the guard.'
        : d.effect === 'tightens' ? ' 🔒 This tightens the guard.' : '';
      const clamp = d.clamped ? ' (clamped into the allowed range)' : '';
      const msg = `${s.label}: ${shown(s, d.before)} → ${shown(s, d.after)}${clamp}.${eff} ${d.saved_sentence || ''}`;
      await load();
      const res2 = document.querySelector(`[data-res="${CSS.escape(key)}"]`);
      if (res2) res2.textContent = msg;
      if (RC.toast) RC.toast(msg);
      return;
    }
    // A refusal (4xx) changed nothing. Anything else (a 5xx, no answer) may
    // follow a write that landed, so it is not called "not changed".
    const status = r && r.status ? r.status : 0;
    const why = d.detail || d.error || (status ? `HTTP ${status}` : 'no answer');
    if (res) {
      res.textContent = (status >= 400 && status < 500)
        ? `Not changed: ${why}`
        : `Not confirmed: ${why} Reload the page to read the value in force.`;
    }
  }

  document.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-act]');
    if (!b) return;
    b.disabled = true;
    change(b.dataset.key, b.dataset.act === 'reset').finally(() => { b.disabled = false; });
  });

  load();
})();
