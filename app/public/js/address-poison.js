/**
 * Address-poisoning warning for Claw Guard v0.1.
 *
 * Before a send, compare the destination with the counterparties the caller
 * already listed (their own history, or a list they pasted). Warn when the
 * destination shares the first 4 and the last 4 hex characters of one of
 * them and is not that address. A second reading flags a zero-value or dust
 * transfer received FROM the destination inside the last 30 days.
 *
 * It warns. It does not block, it does not rewrite the destination, and it
 * does not pick a replacement. An address it cannot read is not "ok".
 *
 * Dust is a raw-unit ceiling (DUST_RAW_MAX), not a price. A value that does
 * not parse is unreadable, and an unreadable row is not "no dust".
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCAddressPoison = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var WINDOW_DAYS = 30;
  var WINDOW_MS = WINDOW_DAYS * 24 * 60 * 60 * 1000;
  /** Inclusive raw ceiling. 10000 base units, of any token or of wei. */
  var DUST_RAW_MAX = 10000n;

  function canon(addr) {
    if (typeof addr !== 'string') return null;
    var m = /^0x([0-9a-fA-F]{40})$/.exec(addr.trim());
    if (!m) return null;
    return m[1].toLowerCase();
  }

  function hex0x(body) {
    return body ? '0x' + body : null;
  }

  /**
   * A raw amount, or null when the value cannot be read as a non-negative
   * integer. A float is unreadable: coercing it would invent a whole number.
   */
  function rawAmount(v) {
    if (typeof v === 'bigint') return v >= 0n ? v : null;
    if (typeof v === 'number') {
      if (!Number.isSafeInteger(v) || v < 0) return null;
      return BigInt(v);
    }
    if (typeof v !== 'string') return null;
    var s = v.trim();
    if (/^0x[0-9a-fA-F]+$/.test(s)) {
      try { return BigInt(s); } catch (e) { return null; }
    }
    if (/^[0-9]+$/.test(s)) {
      try { return BigInt(s); } catch (e) { return null; }
    }
    return null;
  }

  function timeMs(at) {
    if (typeof at === 'number' && Number.isFinite(at)) {
      if (at > 1e12) return at;
      if (at > 1e9) return Math.round(at * 1000);
      return null;
    }
    if (typeof at === 'string' && at.trim()) {
      var t = Date.parse(at);
      if (Number.isFinite(t)) return t;
    }
    return null;
  }

  function isLookalike(dest, other) {
    if (!dest || !other || dest === other) return false;
    return dest.slice(0, 4) === other.slice(0, 4) && dest.slice(-4) === other.slice(-4);
  }

  /**
   * Dust reading of one inbound list against one destination.
   * 'flagged' | 'none' | 'unread' | 'not_checked'
   */
  function dustOf(dest, inbound, now) {
    if (!Array.isArray(inbound)) return 'not_checked';
    if (!Number.isFinite(now)) return 'unread';
    var flagged = false;
    var unread = false;
    for (var i = 0; i < inbound.length; i++) {
      var row = inbound[i];
      if (!row || typeof row !== 'object') continue;
      var from = canon(row.from);
      if (from !== dest) continue;
      var when = timeMs(row.at);
      if (when === null) { unread = true; continue; }
      if (when < now - WINDOW_MS || when > now) continue;
      var amt = rawAmount(row.amount);
      if (amt === null) { unread = true; continue; }
      if (amt <= DUST_RAW_MAX) flagged = true;
    }
    if (flagged) return 'flagged';
    if (unread) return 'unread';
    return 'none';
  }

  /**
   * @returns {{ok:true, result:object} | {ok:false, error:string}}
   * `error` is 'destination' when the address cannot be read. The result
   * never contains a suggested replacement, and `destination` on the input
   * is not written.
   */
  function checkAddressPoisoning(input) {
    var src = input && typeof input === 'object' ? input : {};
    var dest = canon(src.destination);
    if (!dest) return { ok: false, error: 'destination' };
    var list = Array.isArray(src.counterparties) ? src.counterparties : [];
    var compared = 0;
    var match = null;
    for (var i = 0; i < list.length; i++) {
      var c = canon(list[i]);
      if (!c) continue;
      compared += 1;
      if (match) continue;
      if (isLookalike(dest, c)) match = c;
    }
    var now = src.now == null ? Date.now() : (Number.isFinite(src.now) ? src.now : NaN);
    var dust = dustOf(dest, src.inbound, now);
    var level = 'ok';
    var reason = null;
    if (match) {
      level = 'warn';
      reason = 'prefix_suffix_match';
    } else if (dust === 'flagged') {
      level = 'warn';
      reason = 'dust_sender';
    }
    return {
      ok: true,
      result: {
        check: 'address_poisoning',
        level: level,
        lookalike_of: match ? hex0x(match) : null,
        reason: reason,
        dust: dust,
        dust_scope: Array.isArray(src.inbound) ? 'client_list' : null,
        compared: compared,
      },
    };
  }

  /**
   * Fold a public-log reading onto a client-list reading.
   * `logs` is null when no log read was asked for, `{read:false}` when it
   * was asked and could not be read, or a checkAddressPoisoning result.
   * A log clear is ERC-20 logs only: it is `logs_clear`, not `none`.
   */
  function mergePoison(local, logs) {
    var base = local && local.ok ? local.result : null;
    if (!base) return local;
    if (logs == null) return local;
    var out = {
      check: base.check,
      level: base.level,
      lookalike_of: base.lookalike_of,
      reason: base.reason,
      dust: base.dust,
      dust_scope: base.dust_scope,
      compared: base.compared,
    };
    var logDust = 'unread';
    if (logs.read === false) logDust = 'unread';
    else if (logs.ok && logs.result) {
      logDust = logs.result.dust === 'flagged' ? 'flagged'
        : logs.result.dust === 'unread' ? 'unread'
          : 'logs_clear';
    }
    if (base.reason === 'prefix_suffix_match') {
      out.level = 'warn';
      out.reason = 'prefix_suffix_match';
    } else if (base.dust === 'flagged' || logDust === 'flagged') {
      out.level = 'warn';
      out.reason = 'dust_sender';
    } else {
      out.level = 'ok';
      out.reason = null;
      out.lookalike_of = null;
    }
    if (base.dust === 'flagged' || logDust === 'flagged') out.dust = 'flagged';
    else if (base.dust === 'unread' || logDust === 'unread') out.dust = 'unread';
    else if (logDust === 'logs_clear') out.dust = 'logs_clear';
    else out.dust = base.dust;
    if (logDust === 'not_checked') {
      /* a log object was passed but said nothing; leave the client scope */
    } else if (base.dust_scope === 'client_list' && logDust !== 'unread' && base.dust !== 'not_checked') {
      out.dust_scope = 'client_list_and_erc20_logs';
    } else if (base.dust === 'not_checked') {
      out.dust_scope = 'erc20_logs';
    } else {
      out.dust_scope = base.dust_scope || 'erc20_logs';
    }
    return { ok: true, result: out };
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /** Both addresses, with the characters that differ marked. Neither string is shortened. */
  function highlightPair(destination, lookalike) {
    var a = canon(destination);
    var b = canon(lookalike);
    function paint(hex, other) {
      var out = '0x';
      for (var i = 0; i < hex.length; i++) {
        var ch = hex.charAt(i);
        if (other && ch !== other.charAt(i)) out += '<mark class="poison-diff">' + esc(ch) + '</mark>';
        else out += esc(ch);
      }
      return out;
    }
    if (!a) return { destination: esc(destination), lookalike: b ? paint(b, null) : '' };
    return { destination: paint(a, b), lookalike: b ? paint(b, a) : '' };
  }

  return {
    WINDOW_DAYS: WINDOW_DAYS,
    WINDOW_MS: WINDOW_MS,
    DUST_RAW_MAX: DUST_RAW_MAX,
    canon: canon,
    rawAmount: rawAmount,
    isLookalike: isLookalike,
    checkAddressPoisoning: checkAddressPoisoning,
    mergePoison: mergePoison,
    highlightPair: highlightPair,
    esc: esc,
  };
}));
