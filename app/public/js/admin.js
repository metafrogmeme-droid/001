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

  // ── Members ─────────────────────────────────────────────────────────────
  //
  // One read, /api/admin/members, on its own: a members read that fails says
  // so in its own two panels and leaves the settings above standing. A change
  // posts with the 2FA prompt, and the bot answers through the write path the
  // Telegram commands take (bot/core/member_admin.py). Granting live trading
  // or the admin role or tier is Telegram-only and has no control here.

  const MEMBER_ERROR = {
    telegram_required: 'Link your Telegram account to read the members here.',
    gateway_unconfigured: 'The bot gateway is not configured on this site, so the members cannot be read here.',
    operator_only: 'The bot does not recognise your linked Telegram account as an admin.',
    unreachable: 'The bot could not be reached.',
  };
  /** A field the store holds, or a muted "not recorded": absent is not a value. */
  function held(v) {
    return (v === null || v === undefined || v === '')
      ? '<span class="muted">not recorded</span>' : esc(v);
  }

  function liveCell(v) {
    if (v === true) return 'live';
    if (v === false) return 'paper only';
    return '<span class="muted">not read</span>';
  }

  function memberRows(d, canChange) {
    const members = d.members || [];
    if (!members.length) return '<div class="empty">No members registered.</div>';
    const dis = canChange ? '' : ' disabled';
    // Each select opens on the member's current value where the website may
    // set it, so a tap on the button beside it changes only what was changed.
    const opts = (list, current) => (list || []).map((v) =>
      `<option value="${esc(v)}"${v === current ? ' selected' : ''}>${esc(v)}</option>`).join('');
    const body = members.map((m) => {
      const roleOpts = opts(d.web_roles, m.role);
      const tierOpts = opts(d.web_tiers, m.tier);
      const id = String(m.id || '');
      const self = id === String(d.you || '');
      const vouchable = /^[0-9]+$/.test(id);
      const acts = [];
      // An admin account is changed from Telegram only (member_admin.py), so
      // its row offers nothing that would only be refused.
      const isAdmin = m.role === 'admin' || m.tier === 'admin';
      if (isAdmin) {
        acts.push('<span class="muted">Changed from Telegram only</span>');
      } else {
        if (vouchable) {
          acts.push(`<select data-mrole="${esc(id)}"${dis} aria-label="Role to approve as">${roleOpts}</select>`
            + `<button class="btn btn--sm" type="button" data-mact="approve" data-mid="${esc(id)}"${dis}>Approve</button>`);
        }
        acts.push(`<select data-mtier="${esc(id)}"${dis} aria-label="Tier">${tierOpts}</select>`
          + `<button class="btn btn--sm" type="button" data-mact="set_tier" data-mid="${esc(id)}"${dis}>Set tier</button>`);
        if (vouchable && m.live === true) {
          acts.push(`<button class="btn btn--sm" type="button" data-mact="revoke_live" data-mid="${esc(id)}"${dis}>Paper only</button>`);
        }
        if (vouchable && !self) {
          acts.push(`<button class="btn btn--sm" type="button" data-mact="revoke" data-mid="${esc(id)}"${dis}>Revoke</button>`);
        }
      }
      return `<tr><td class="mono" data-label="Member">${esc(id)}</td>
        <td data-label="Name">${held(m.name)}</td>
        <td data-label="Role">${held(m.role)}${m.authorized === false ? ' <span class="warn">(revoked)</span>' : ''}</td>
        <td data-label="Tier">${held(m.tier)}</td>
        <td data-label="Trading">${liveCell(m.live)}</td>
        <td data-label="Admitted by">${held(m.admitted_by)}</td>
        <td data-label="Change"><div class="mem-act">${acts.join('')}</div><div class="mem-note" data-mres="${esc(id)}"></div></td></tr>`;
    }).join('');
    let note = canChange ? '' : '<p class="note">Changing a member needs two-factor sign-in on this account. Reading works, and Telegram still works.</p>';
    if (typeof d.members_total === 'number' && d.members_total > members.length) {
      note += `<p class="note">Showing the newest ${esc(members.length)} of ${esc(d.members_total)} members.</p>`;
    }
    // The same caveat /users prints: a store that started empty is a list of
    // who has messaged since, not of who is registered.
    if (d.store_started_empty === true) {
      note += '<div class="warn">The bot\'s user store started empty on this run, so this list is everyone who has messaged since, not necessarily everyone who was registered before. Check the data/ symlink and any recent restore.</div>';
    }
    return `${note}<table class="rows"><thead><tr><th>Member</th><th>Name</th><th>Role</th><th>Tier</th><th>Trading</th><th>Admitted by</th><th>Change</th></tr></thead><tbody>${body}</tbody></table>`;
  }

  function memberChangeRows(rows) {
    if (!rows.length) return '<div class="empty">No member changes recorded yet.</div>';
    const pick = (s) => (s && typeof s === 'object')
      ? [s.role, s.tier, s.live === true ? 'live' : s.live === false ? 'paper' : null].filter(Boolean).join(' · ')
      : 'no record';
    const body = rows.map((r) => {
      const at = r.at ? new Date(r.at) : null;
      const when = at && !isNaN(at.getTime()) ? at.toLocaleString() : 'time not recorded';
      const what = String(r.action || '').replace('_', ' ');
      const held2 = r.held ? ' <span class="warn">(in force, not saved)</span>' : '';
      return `<tr><td class="mono" data-label="When">${esc(when)}</td><td data-label="Change">${esc(what)}${held2}</td>
        <td class="mono" data-label="Member">${esc(r.target || '')}</td>
        <td data-label="Before → after">${esc(pick(r.before))} → ${esc(pick(r.after))}</td>
        <td data-label="Who">${esc(r.via || '')} · ${esc(r.actor || '')}</td></tr>`;
    }).join('');
    return `<table class="rows"><thead><tr><th>When</th><th>Change</th><th>Member</th><th>Before → after</th><th>Who</th></tr></thead><tbody>${body}</tbody></table>`;
  }

  function paintMembers(d) {
    if (!d.bot) {
      const why = MEMBER_ERROR[d.bot_error] || `The bot answered with an error (${esc(d.bot_error || 'unknown')}).`;
      ['admMembers', 'admMemberChanges'].forEach((id) => { $(id).innerHTML = `<div class="warn">${esc(why)}</div>`; });
      return;
    }
    const b = d.bot;
    const canChange = Boolean(d.two_factor);
    $('admMembers').innerHTML = Array.isArray(b.members)
      ? memberRows(b, canChange)
      : `<div class="warn">The members could not be read (${esc(b.members_error || 'unknown')}).</div>`;
    $('admMemberChanges').innerHTML = Array.isArray(b.changes)
      ? memberChangeRows(b.changes)
      : `<div class="warn">The member change record could not be read (${esc(b.changes_error || 'unknown')}).</div>`;
  }

  async function loadMembers() {
    const r = await RC.fetchJSON('/api/admin/members', { timeoutMs: 20000 })
      .catch(() => ({ ok: false, status: 0, data: null }));
    if (r.status === 401 || r.status === 403) {
      ['admMembers', 'admMemberChanges'].forEach((id) => { $(id).innerHTML = ''; });
      return undefined;
    }
    if (!r.ok || r.unreadable || !r.data || typeof r.data !== 'object') {
      const msg = `The members could not be read${r.status ? ` (HTTP ${esc(r.status)})` : ''}. Try again shortly.`;
      ['admMembers', 'admMemberChanges'].forEach((id) => { $(id).innerHTML = `<div class="warn">${msg}</div>`; });
      return undefined;
    }
    return paintMembers(r.data);
  }

  async function memberChange(action, id) {
    const res = document.querySelector(`[data-mres="${CSS.escape(id)}"]`);
    const body = { action, target: id };
    if (action === 'approve') {
      const sel = document.querySelector(`[data-mrole="${CSS.escape(id)}"]`);
      body.role = sel ? sel.value : '';
    }
    if (action === 'set_tier') {
      const sel = document.querySelector(`[data-mtier="${CSS.escape(id)}"]`);
      body.tier = sel ? sel.value : '';
    }
    const say = {
      approve: `Approve ${id} as ${body.role}?`,
      set_tier: `Set ${id}'s tier to ${body.tier}?`,
      revoke_live: `Restrict ${id} to paper trading?`,
      revoke: `Revoke ${id}'s access?`,
    }[action];
    if (!window.confirm(say)) return;
    if (res) res.textContent = 'Applying…';
    const r = await RC.postWithStepUp('/api/admin/members', body, { timeoutMs: 20000 });
    const d = (r && r.data) || {};
    const status = r && r.status ? r.status : 0;
    let msg;
    if (r && r.ok && d.ok) {
      msg = 'Done.' + (d.notified === false ? ' The member could not be told (they may not have started the bot).' : '');
    } else if (d.held) {
      msg = `In force, NOT saved: ${d.detail || 'the store could not be written'}. A restart goes back to what is on disk.`;
    } else if (status >= 400 && status < 500) {
      msg = `Not changed: ${d.detail || d.error || `HTTP ${status}`}`;
    } else {
      msg = `Not confirmed: ${d.detail || d.error || (status ? `HTTP ${status}` : 'no answer')} Reload to read the member as they stand.`;
    }
    await loadMembers();
    const res2 = document.querySelector(`[data-mres="${CSS.escape(id)}"]`);
    if (res2) res2.textContent = msg;
    if (RC.toast) RC.toast(msg);
  }

  document.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-mact]');
    if (!b) return;
    b.disabled = true;
    memberChange(b.dataset.mact, b.dataset.mid).finally(() => { b.disabled = false; });
  });

  load();
  loadMembers();
})();
