/*
 * The bridge check on /risk: what /api/risk/bridge answered, as sentences.
 *
 * A COUNT, NEVER A VERDICT. Each direction says how many verifiers must agree
 * and who they are. Colour is a claim, so amber is kept for the two settings
 * where one party, or none, decides what the app takes; a larger count is
 * plain text, never green, because a count says nothing about who runs them.
 *
 * NOT READ IS NEVER SAFE. A 400 asks the visitor to check the question; any
 * other answer that is not a reading (503, a reader down, readers that
 * disagree, a body that does not parse) is "could not be read", and a library
 * the check does not know is named, not counted.
 *
 * Pure, with the words passed in, so app/test/a_bridge_is_read_for_how_many_verifiers_it_needs.test.js
 * renders it.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.RCRiskBridge = api;
}(typeof window !== 'undefined' ? window : null, function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /** The words, in English; the page passes T(key, english) so each is translated. */
  var EN = {
    'rk.r_unread': 'Could not be read: not a verdict either way.',
    'rk.refused': 'Check the chain, the address and the endpoint id.',
    'rk.r_lib': 'A library this check does not know: not read',
    'rk.r_unnamed': 'not in LayerZero’s list',
    'rk.r_none': 'No verifier has to agree',
    'rk.r_one': 'One verifier’s word is enough',
    'rk.r_n': '{n} verifiers must agree',
    'rk.r_in': 'Messages into this app',
    'rk.r_out': 'Messages it sends',
    'rk.r_by': 'Read by {readers}, who agreed.',
  };
  function english(k) { return EN[k]; }

  /** One direction's count, as a sentence and whether it earns the amber. */
  function countOf(s, t) {
    if (s.no_verifier === true) return { text: t('rk.r_none', EN['rk.r_none']), amber: true };
    if (s.single_verifier === true) return { text: t('rk.r_one', EN['rk.r_one']), amber: true };
    if (typeof s.verifiers_needed !== 'number') return null;
    return { text: t('rk.r_n', EN['rk.r_n']).replace('{n}', String(s.verifiers_needed)), amber: false };
  }

  function sideHtml(title, s, t) {
    if (!s) return '';
    var head = '<div class="rk-side"><h3>' + esc(title) + '</h3>';
    if (s.library_known === false) {
      return head + '<p class="muted">' + esc(t('rk.r_lib', EN['rk.r_lib'])) + ' <span class="rk-who">' + esc(s.library) + '</span></p></div>';
    }
    var c = countOf(s, t);
    if (!c) return head + '<p class="muted">' + esc(t('rk.r_unread', EN['rk.r_unread'])) + '</p></div>';
    var who = (s.required || []).concat(s.optional || []).map(function (v) {
      return esc(v.name || t('rk.r_unnamed', EN['rk.r_unnamed'])) + ' <span class="muted">' + esc(v.address) + '</span>';
    }).join('<br>');
    return head + '<p><span class="rk-count' + (c.amber ? ' rk-one' : '') + '">' + esc(c.text) + '</span></p>'
      + '<div class="rk-who">' + who + '</div></div>';
  }

  /**
   * What to show for one answer: {html} for a reading, {text} otherwise.
   * `status` is the HTTP status, `body` the parsed JSON or null.
   */
  function view(status, body, t) {
    t = t || function (k, en) { return en; };
    var b = body || {};
    if (status === 400) return { text: t('rk.refused', EN['rk.refused']) };
    if (status !== 200 || b.status !== 'read' || !b.receive || !b.send) return { text: t('rk.r_unread', EN['rk.r_unread']) };
    var readers = Array.isArray(b.readers) ? b.readers : [];
    return {
      html: sideHtml(t('rk.r_in', EN['rk.r_in']), b.receive, t)
        + sideHtml(t('rk.r_out', EN['rk.r_out']), b.send, t)
        + '<p class="note">' + esc(t('rk.r_by', EN['rk.r_by']).replace('{readers}', readers.join(', '))) + '</p>',
    };
  }

  return { view: view, sideHtml: sideHtml, countOf: countOf, english: english, esc: esc };
}));
