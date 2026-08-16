/* RUNECLAW — the meme swap review-and-sign page.
 *
 * Thin on purpose. Every decision that matters is made in two pure modules
 * that have their own tests — `swap-sign-model.js` (may this be signed, right
 * now, by this wallet) and `solana_wallet.js` (hand it to the wallet) — and
 * what is left here is DOM plumbing. That split is CLAUDE.md's "when there is
 * no seam, make one", applied to the one page in this app where a wrong "yes"
 * spends money.
 *
 * Three things this file must never do, all of which it would do by default:
 *
 *   1. Render a failed read as a result. Every catch paints an error; none
 *      returns quietly to a page still showing the last good numbers.
 *   2. Say "sent" without a signature. `signAndSend` throws on every failure
 *      including a wallet that returned nothing, and only a real signature
 *      reaches the success path.
 *   3. Re-derive whether signing is allowed. The server says so in `signable`
 *      and the model enforces it; this file only shows the answer.
 */
(function () {
  'use strict';

  const M = window.SwapSignModel;
  const $ = (id) => document.getElementById(id);

  // Intents this browser has already sent. Not a substitute for the server
  // knowing, but it catches the common case — a double-click, or a user who
  // hits Sign again after the wallet popup was slow.
  const sent = new Set();

  let build = null;
  let ticker = null;

  function setStatus(msg, kind) {
    const el = $('status');
    if (!el) return;
    el.textContent = msg || '';
    el.className = 'status' + (kind ? ' ' + kind : '');
  }

  function connectedWallet() {
    const el = $('wallet');
    return (el && el.dataset.address) || null;
  }

  async function connect() {
    if (!window.RCSolanaWallet || !window.RCSolanaWallet.available()) {
      setStatus('No Solana wallet found. Install Phantom or Backpack.', 'err');
      return;
    }
    try {
      const { address } = await window.RCSolanaWallet.connect();
      const el = $('wallet');
      el.dataset.address = address;
      el.textContent = address.slice(0, 4) + '…' + address.slice(-4);
      setStatus('Wallet connected. RUNECLAW never holds your keys.', 'ok');
      refresh();
    } catch (err) {
      setStatus(err && err.message ? err.message : 'Could not connect.', 'err');
    }
  }

  async function doBuild() {
    const mint = ($('mint').value || '').trim();
    const size = ($('size').value || '').trim();
    const wallet = connectedWallet();
    if (!wallet) { setStatus('Connect a wallet first.', 'err'); return; }

    build = null;
    render();
    setStatus('Reading the market and building…', '');
    let r;
    try {
      r = await fetch('/api/meme/swap/build', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ mint: mint, size_usd: Number(size),
          user_public_key: wallet }),
        signal: AbortSignal.timeout(30000),
      });
    } catch (err) {
      // An unreachable planner is not an empty plan. Saying "no route found"
      // here would report a market fact we never established.
      setStatus('Could not reach the planner — nothing was built.', 'err');
      return;
    }
    let data = null;
    try { data = await r.json(); } catch (_) { data = null; }
    if (!r.ok) {
      setStatus((data && (data.detail || data.error))
        || 'The build failed — nothing was built or signed.', 'err');
      return;
    }
    $('plan').textContent = (data && data.human) || '';
    build = (data && data.build) || null;
    if (!build) {
      setStatus((data && data.reason) || 'The plan did not pass — nothing was built.', 'warn');
    } else {
      setStatus('', '');
    }
    render();
  }

  function render() {
    const card = $('card');
    if (!build) { card.hidden = true; stopTicker(); return; }
    const c = M.reviewCells(build);
    card.hidden = false;
    $('c-net').textContent = c.network;
    $('c-in').textContent = c.inAmount;
    $('c-out').textContent = c.outAmount;
    $('c-min').textContent = c.minReceived;
    $('c-slip').textContent = c.slippage;
    const imp = $('c-impact');
    imp.textContent = c.priceImpact;
    imp.className = c.impactClass;
    $('c-intent').textContent = c.intent;
    $('c-custody').textContent = c.custody;
    $('c-caveat').textContent = c.networkCaveat;

    const banner = $('c-review-only');
    banner.hidden = !c.reviewOnly;
    banner.textContent = c.reviewOnly ? c.reviewOnlyReason : '';

    // The mainnet confirmation is only ever offered for a build the server
    // says may be signed. Showing it beside a review-only build would invite a
    // click that can only be refused.
    $('confirm-row').hidden = !(c.isMainnet && !c.reviewOnly);
    startTicker();
    refresh();
  }

  /** Re-ask the model. Cheap, pure, and the only source of the button state. */
  function refresh() {
    const btn = $('sign');
    const why = $('why');
    if (!build) { btn.disabled = true; why.textContent = ''; return; }
    const verdict = M.canSign(build, {
      nowMs: Date.now(),
      connectedWallet: connectedWallet(),
      sentIntents: sent,
      mainnetConfirmed: $('confirm') && $('confirm').checked,
    });
    btn.disabled = !verdict.ok;
    why.textContent = verdict.reason;
    why.className = 'why ' + (verdict.ok ? 'ok' : 'no');
    const left = M.secondsLeft(build, Date.now());
    // Only shown when it is actually the binding constraint — a countdown next
    // to a permanently unsignable build implies waiting would help.
    $('c-ttl').textContent = (verdict.ok || verdict.code === 'expired')
      ? (left > 0 ? left + 's left on this quote' : 'quote expired') : '';
  }

  function startTicker() {
    stopTicker();
    // Expiry is checked at CLICK time by the model; this only keeps the label
    // and the disabled state honest while the user reads.
    ticker = setInterval(refresh, 1000);
  }

  function stopTicker() {
    if (ticker) { clearInterval(ticker); ticker = null; }
  }

  async function doSign() {
    const verdict = M.canSign(build, {
      nowMs: Date.now(),
      connectedWallet: connectedWallet(),
      sentIntents: sent,
      mainnetConfirmed: $('confirm') && $('confirm').checked,
    });
    // Re-asked here rather than trusting the button: the disabled state was
    // computed up to a second ago, and a quote can die in that second.
    if (!verdict.ok) { setStatus(verdict.reason, 'err'); refresh(); return; }

    $('sign').disabled = true;
    setStatus('Approve in your wallet…', '');
    try {
      const { signature } = await window.RCSolanaWallet
        .signAndSend(build.unsigned_transaction);
      sent.add(build.intent_id);
      // "Submitted", not "done". A signature means the wallet accepted and
      // forwarded it; it does not mean the transaction confirmed, and it
      // certainly does not mean the swap filled at the quoted price.
      setStatus('Submitted to the network — signature ' + signature
        + '. This is not yet a confirmation; check your wallet or an explorer '
        + 'for the final result.', 'ok');
    } catch (err) {
      setStatus((err && err.message) || 'The wallet refused — nothing was sent.', 'err');
    }
    refresh();
  }

  document.addEventListener('DOMContentLoaded', function () {
    $('connect').addEventListener('click', connect);
    $('build').addEventListener('click', doBuild);
    $('sign').addEventListener('click', doSign);
    const cb = $('confirm');
    if (cb) cb.addEventListener('change', refresh);
    refresh();
  });
}());
