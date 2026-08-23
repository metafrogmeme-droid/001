/**
 * The dashboard venue picker's state, as a PURE function.
 *
 * The web twin of `bot/formatters/venue_card.py`, and it exists for the same
 * reason that one does: the claim this UI can most easily get wrong is not a
 * number, it is whether the thing the user just ticked is DOING anything.
 *
 * `routes/controls.js` already carries the scar. Pause-to-paper stored a
 * preference, acked it, and the website showed the user as paused while every
 * confirmed trade still went to the exchange. Three separate states have to
 * stay distinguishable here or the same failure repeats with venues:
 *
 *   * PROPOSED — you ticked it, the bot has not seen it yet
 *   * APPLIED  — the bot holds it
 *   * IN FORCE — the bot holds it AND multi-venue routing is enforcing
 *
 * A tick that means the first while looking like the third is somebody sizing
 * their risk against a diversification they do not have.
 *
 * `mode: null` is a fourth state and is NOT `off`. It means the bot has not
 * acked yet, and rendering it as "multi-venue is off" would be a confident
 * claim about a control nobody has reported on.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.VenuePickerModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /**
   * @param {object} s  /api/controls/status payload
   * @param {Array}  connected  [{venue, connected}] from /api/credentials/status
   * @returns {{rows: Array, notice: {tone: string, text: string}|null,
   *            dirty: boolean, canSave: boolean}}
   */
  function pickerState(s, connected) {
    const st = s || {};
    const conn = (connected || [])
      .filter((v) => v && v.connected)
      .map((v) => String(v.venue || 'bitget').toLowerCase());

    const applied = Array.isArray(st.venues) ? st.venues.map(String) : [];
    // null means "nothing in flight" — distinct from [] which means "a request
    // to clear is in flight". Collapsing them would show a pending clear as no
    // pending change at all.
    const pending = st.venues_pending === null || st.venues_pending === undefined
      ? null : st.venues_pending.map(String);
    const shown = pending !== null ? pending : applied;
    const mode = st.venues_mode === null || st.venues_mode === undefined
      ? null : String(st.venues_mode);

    const rows = conh(conn).map((v) => ({
      venue: v,
      checked: shown.includes(v),
      // A venue that is SELECTED but no longer connected still has to appear,
      // or the user cannot tell "I turned this off" from "my keys stopped
      // working". It appears as a problem, never as an unticked option.
      disconnected: false,
    }));
    for (const v of shown) {
      if (!conn.includes(v)) {
        rows.push({ venue: v, checked: true, disconnected: true });
      }
    }

    return {
      rows,
      notice: notice(mode, pending, shown),
      dirty: pending !== null,
      // Nothing to save when nothing is connected — and saying so beats a
      // button that fails.
      canSave: conn.length > 0,
    };
  }

  /** Sorted unique connected venues. */
  function conh(list) {
    return [...new Set(list)].sort();
  }

  /**
   * The line above the checkboxes. It outranks them: a reader who takes the
   * ticks at face value while this says "off" has misunderstood their risk.
   */
  function notice(mode, pending, shown) {
    if (pending !== null) {
      return { tone: 'pending',
        text: 'Queued — the bot applies this within a minute. Until it does, '
            + 'your trades still use the venues shown before this change.' };
    }
    if (mode === null) {
      // NOT "off". We have not been told.
      return { tone: 'unknown',
        text: 'Waiting for the bot to report which venues are in force. What '
            + 'is ticked here is your saved choice, not a confirmation.' };
    }
    if (!shown.length) {
      return { tone: 'info',
        text: 'No venues chosen — trading uses your single default venue.' };
    }
    if (mode === 'off') {
      return { tone: 'warn',
        text: `You have chosen ${shown.length} venues, but multi-venue trading `
            + 'is OFF on this deployment. Every order still goes to your single '
            + 'default venue — your book is NOT spread across them.' };
    }
    if (mode === 'shadow') {
      return { tone: 'warn',
        text: `Shadow mode: the bot records which of your ${shown.length} venues `
            + 'each order would use, and still sends it to your single default '
            + 'venue. Nothing is spread yet.' };
    }
    return { tone: 'ok',
      text: `Orders are routed across your ${shown.length} venues. Each trade `
          + 'goes to ONE of them — the one with the most free margin — never to '
          + 'all of them.' };
  }

  return { pickerState, notice };
}));
