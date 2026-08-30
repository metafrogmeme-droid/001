'use strict';
/**
 * The ERC-8257 registration, sent from the operator's own wallet.
 *
 * This page exists because Phantom — and most wallets — have no UI for a raw
 * contract call. Something has to build the transaction and hand it over. The
 * server cannot: it holds no key, never signs, never broadcasts. So the
 * browser does, and the wallet signs.
 *
 * WHAT MAKES IT WORTH HAVING RATHER THAN TYPING THE FIELDS INTO AN EXPLORER:
 * it recomputes the manifest hash here, from the published preimage, with an
 * implementation that is not the server's copy of ethers. For a while this
 * endpoint served
 *
 *     0x848ee4da…3a97   — plain SHA-256, from a stubbed ethers
 *
 * where the true keccak256 was 0x7fe3e5ec…b053. Both are well-formed 32-byte
 * hex. Nothing on the surface could tell them apart, and the wrong one would
 * have been written to Base permanently. The Send button below is disabled
 * until the recomputed hash matches the served one and the calldata carries
 * the right selector — so the check that was missing is the check that gates
 * the transaction.
 *
 * NOTHING HERE IS A SECRET. The key never leaves the wallet; this file builds
 * a transaction object and calls eth_sendTransaction. If the page is wrong the
 * worst it can do is refuse to send.
 */
(function () {
  'use strict';

  const REGISTRY_CHAIN_ID = 8453;              // Base
  const CHAIN_HEX = '0x' + REGISTRY_CHAIN_ID.toString(16);
  const REGISTER_SELECTOR = '0xfe1d0b16';      // registerTool(string,bytes32,address)

  const $ = (id) => document.getElementById(id);
  const text = (id, s) => { const el = $(id); if (el) el.textContent = s; };

  /** A row's verdict. Three-valued — "not checked yet" is not "passed". */
  function verdict(id, state, detail) {
    const el = $(id);
    if (!el) return;
    const icon = { pass: '✅', fail: '❌', unknown: '⚪' }[state] || '⚪';
    el.className = 'v ' + state;
    el.textContent = `${icon} ${detail}`;
  }

  let plan = null;
  let checksPassed = false;

  async function load() {
    verdict('vFetch', 'unknown', 'fetching the registration plan…');
    let data;
    try {
      const r = await fetch('/api/tool/registration-plan', { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      data = await r.json();
    } catch (e) {
      // GUARD, not a blank panel. A failed read must never render as a page
      // that merely looks empty next to an enabled button.
      //
      // AND IT MUST DISCARD WHAT IT HAD. `load()` is also the Re-check button,
      // so a successful load followed by a failed one used to leave `plan` and
      // `checksPassed` from the previous attempt — Send stayed live, wired to
      // data the page had just said it could not read. The markup's `disabled`
      // covers the first load only; nothing covered the second.
      plan = null;
      checksPassed = false;
      $('send').disabled = true;
      verdict('vFetch', 'fail', 'could not read the plan — ' + (e.message || e));
      text('why', 'Nothing was checked, so nothing can be sent.');
      return;
    }
    plan = data;
    verdict('vFetch', 'pass', 'plan loaded');
    render();
  }

  function render() {
    const p = plan || {};
    text('fHash', p.manifest_hash || '—');
    text('fUri', p.metadata_uri || '—');
    text('fRegistry', p.registry || '—');
    text('fPredicate', p.access_predicate || '—');
    text('fCalldata', p.calldata || '—');

    let creator = '—', endpoint = '—';
    try {
      const m = JSON.parse(p.manifest_canonical || '{}');
      creator = m.creatorAddress || '—';
      endpoint = m.endpoint || '—';
    } catch (e) { /* left as em dash — absent, not guessed */ }
    text('fCreator', creator);
    text('fEndpoint', endpoint);

    const reasons = [];

    // 1. THE HASH. Recomputed here from the published preimage.
    let hashOk = false;
    if (!p.manifest_canonical || !p.manifest_hash) {
      verdict('vHash', 'fail', 'the plan carries no hash or no preimage');
      reasons.push('no hash to verify');
    } else if (typeof Keccak256 === 'undefined') {
      verdict('vHash', 'fail', 'the keccak256 module did not load — cannot verify');
      reasons.push('verifier missing');
    } else {
      try {
        const mine = Keccak256.keccak256Utf8(p.manifest_canonical);
        hashOk = mine.toLowerCase() === String(p.manifest_hash).toLowerCase();
        verdict('vHash', hashOk ? 'pass' : 'fail', hashOk
          ? 'recomputed keccak256 matches the served hash'
          : `MISMATCH — recomputed ${mine}`);
        if (!hashOk) reasons.push('the served hash is not keccak256 of the preimage');
      } catch (e) {
        verdict('vHash', 'fail', 'recompute threw — ' + (e.message || e));
        reasons.push('verifier failed');
      }
    }

    // 2. THE CALLDATA. '0x' is a transaction that succeeds and does nothing.
    const cd = String(p.calldata || '');
    let cdOk = false;
    if (cd.length <= 2) {
      verdict('vCalldata', 'fail', 'calldata is EMPTY — that call registers nothing');
      reasons.push('empty calldata');
    } else if (cd.slice(0, 10).toLowerCase() !== REGISTER_SELECTOR) {
      verdict('vCalldata', 'fail', `selector ${cd.slice(0, 10)} is not registerTool`);
      reasons.push('wrong selector');
    } else if (p.manifest_hash && !cd.toLowerCase().includes(
      String(p.manifest_hash).slice(2).toLowerCase())) {
      verdict('vCalldata', 'fail', 'the calldata does not carry the hash shown above');
      reasons.push('calldata and hash disagree');
    } else {
      cdOk = true;
      verdict('vCalldata', 'pass', `registerTool, ${(cd.length - 2) / 2} bytes, carries the hash`);
    }

    // 3. WHAT THE SERVER ITSELF SAYS. Its own not-ready reasons still count.
    const readyOk = p.ready === true;
    verdict('vReady', readyOk ? 'pass' : 'fail', readyOk
      ? 'the plan reports ready'
      : 'not ready — ' + ((p.not_ready_reasons || []).join('; ') || 'no reason given'));
    if (!readyOk) reasons.push('plan not ready');

    // 4. DRIFT. Three-valued: a plan with nothing recorded cannot say it matches.
    if (p.registration_check === 'drifted') {
      verdict('vDrift', 'fail', 'ALREADY REGISTERED at a different hash — '
        + 'sending again registers a second record');
    } else if (p.registration_check === 'matches') {
      verdict('vDrift', 'pass', 'matches the recorded registration — already registered');
    } else {
      verdict('vDrift', 'unknown',
        'no registered hash recorded, so a prior registration cannot be detected');
    }

    checksPassed = hashOk && cdOk && readyOk;
    const btn = $('send');
    btn.disabled = !checksPassed;
    text('why', checksPassed
      ? 'All checks pass. Your wallet will ask you to confirm; the key never leaves it.'
      : 'Send is disabled: ' + reasons.join(', ') + '.');
  }

  async function send() {
    const btn = $('send');
    if (!checksPassed || !plan) return;              // belt and braces
    const eth = window.ethereum;
    if (!eth) {
      text('status', 'No wallet found. Open this page inside your wallet’s browser, '
        + 'or install the extension.');
      return;
    }
    btn.disabled = true;
    try {
      text('status', 'Approving the account…');
      const accounts = await eth.request({ method: 'eth_requestAccounts' });
      const from = accounts && accounts[0];
      if (!from) throw new Error('no account');

      text('status', 'Switching to Base…');
      try {
        await eth.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: CHAIN_HEX }] });
      } catch (sw) {
        if (sw && sw.code === 4902) {
          await eth.request({ method: 'wallet_addEthereumChain', params: [{
            chainId: CHAIN_HEX, chainName: 'Base',
            nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 },
            rpcUrls: ['https://mainnet.base.org'],
            blockExplorerUrls: ['https://basescan.org'],
          }] });
        } else { throw sw; }
      }

      text('status', 'Confirm in your wallet — value 0, you pay only Base gas.');
      const tx = await eth.request({ method: 'eth_sendTransaction', params: [{
        from, to: plan.registry, data: plan.calldata, value: '0x0',
      }] });
      text('status', '✓ Sent: ' + tx);
      const done = $('afterSend');
      if (done) done.hidden = false;
      text('recordCmd', 'REGISTERED_MANIFEST_HASH=' + plan.manifest_hash);
    } catch (err) {
      // Not sent is a state worth naming plainly — a cancelled signature and a
      // failed one look identical from a blank screen.
      text('status', 'Not sent — ' + ((err && (err.message || err.code)) || 'cancelled')
        + '. Nothing was registered.');
      btn.disabled = !checksPassed;
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    $('send').addEventListener('click', send);
    $('reload').addEventListener('click', load);
    load();
  });
}());
