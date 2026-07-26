/**
 * Transaction X-ray — decode what a transaction actually does BEFORE signing.
 *
 * Pure, dependency-free (BigInt slicing, no ABI library), usable from the
 * browser and via require() on the server — the same both-sides pattern as
 * firewall-model.js. Stores nothing, reads no chain.
 *
 * Honesty contract:
 * - Decoding is exact for the known selector set (each selector is pinned to
 *   its signature by keccak in tests). Everything else is UNKNOWN — and
 *   unknown means unknown, never "safe" and never "dangerous".
 * - Token amounts are RAW UNITS: without the token's decimals (a chain read
 *   this module deliberately does not do) a human number would be invented.
 *   Say raw, flag huge.
 * - Flags are heuristic reads with reasons, never verdicts.
 * - Every action carries {tid, params} beside assembled English, so the UI
 *   renders it in any language and falls back whole, never mixed.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCTxRay = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // selector -> decoder. Signatures pinned by keccak in test/txray.test.js.
  var UNLIMITED = (1n << 128n); // >= 2^128 raw units is not a human allowance

  function word(body, i) { return body.slice(i * 64, i * 64 + 64); }
  function addr(body, i) { return '0x' + word(body, i).slice(24); }
  function uint(body, i) { return BigInt('0x' + (word(body, i) || '0')); }

  function weiToEth(v) {
    var int = v / 1000000000000000000n, frac = (v % 1000000000000000000n)
      .toString().padStart(18, '0').replace(/0+$/, '');
    return frac ? int + '.' + frac : int.toString();
  }

  function act(tid, en, params) { return { tid: tid, en: en, params: params || {} }; }
  function flag(id, sev) { return { id: id, sev: sev }; }

  function amountParams(v) {
    return v >= UNLIMITED
      ? { amount: 'UNLIMITED', unlimited: true }
      : { amount: v.toString(), unlimited: false };
  }

  var DECODERS = {
    '0x095ea7b3': function (b, out) { // approve(address,uint256) — ERC-20 AND ERC-721 share it
      var v = uint(b, 1), p = amountParams(v);
      p.spender = addr(b, 0);
      out.actions.push(act('txr.a_approve',
        'Grants {spender} an allowance of {amount} raw token units (for an NFT contract, this number is a token id instead).', p));
      if (v >= UNLIMITED) out.flags.push(flag('unlimited_approval', 'high'));
    },
    '0x39509351': function (b, out) { // increaseAllowance(address,uint256)
      var v = uint(b, 1), p = amountParams(v);
      p.spender = addr(b, 0);
      out.actions.push(act('txr.a_increase',
        'Increases {spender}’s allowance by {amount} raw token units.', p));
      if (v >= UNLIMITED) out.flags.push(flag('unlimited_approval', 'high'));
    },
    '0xa9059cbb': function (b, out) { // transfer(address,uint256)
      var p = amountParams(uint(b, 1)); p.to = addr(b, 0);
      out.actions.push(act('txr.a_transfer',
        'Sends {amount} raw token units to {to}.', p));
    },
    '0x23b872dd': function (b, out) { // transferFrom(address,address,uint256)
      var p = amountParams(uint(b, 2)); p.from = addr(b, 0); p.to = addr(b, 1);
      out.actions.push(act('txr.a_transfer_from',
        'Moves {amount} raw token units (or one NFT by id) from {from} to {to} — requires an existing approval.', p));
    },
    '0xd505accf': function (b, out) { // permit(owner,spender,value,deadline,v,r,s)
      var v = uint(b, 2), p = amountParams(v);
      p.owner = addr(b, 0); p.spender = addr(b, 1);
      out.actions.push(act('txr.a_permit',
        'Submits a SIGNED permit: {owner} grants {spender} an allowance of {amount} raw token units — an approval that moved by signature, without its own transaction.', p));
      out.flags.push(flag('gasless_permit', 'medium'));
      if (v >= UNLIMITED) out.flags.push(flag('unlimited_approval', 'high'));
    },
    '0xa22cb465': function (b, out) { // setApprovalForAll(address,bool)
      var on = uint(b, 1) !== 0n;
      out.actions.push(act(on ? 'txr.a_forall_on' : 'txr.a_forall_off',
        on ? 'Grants {operator} control over EVERY token this wallet holds in this collection — the classic drain primitive.'
           : 'Revokes {operator}’s control over this collection.',
        { operator: addr(b, 0) }));
      if (on) out.flags.push(flag('approval_for_all', 'high'));
    },
    '0x42842e0e': erc721Safe, '0xb88d4fde': erc721Safe,
    '0xf242432a': function (b, out) { // 1155 safeTransferFrom(from,to,id,amount,bytes)
      out.actions.push(act('txr.a_safe_transfer',
        'Transfers NFT id {id} from {from} to {to}.',
        { from: addr(b, 0), to: addr(b, 1), id: uint(b, 2).toString() }));
    },
    '0xac9650d8': function (b, out, depth) { // multicall(bytes[])
      var arrOff = Number(uint(b, 0)) / 32;
      var n = Number(uint(b, arrOff));
      out.actions.push(act('txr.a_multicall',
        'A batch of {n} inner calls — each one below is decoded on its own.', { n: n }));
      for (var k = 0; k < n && k < 20; k++) {
        var elOff = arrOff + 1 + Number(uint(b, arrOff + 1 + k)) / 32;
        var len = Number(uint(b, elOff));
        var inner = '0x' + b.slice((elOff + 1) * 64, (elOff + 1) * 64 + len * 2);
        var sub = decodeTx({ data: inner }, (depth || 0) + 1);
        out.actions = out.actions.concat(sub.actions);
        out.flags = out.flags.concat(sub.flags);
      }
    },
  };

  function erc721Safe(b, out) { // safeTransferFrom(address,address,uint256[,bytes])
    out.actions.push(act('txr.a_safe_transfer',
      'Transfers NFT id {id} from {from} to {to}.',
      { from: addr(b, 0), to: addr(b, 1), id: uint(b, 2).toString() }));
  }

  function decodeTx(tx, depth) {
    var out = { actions: [], flags: [], unknown: false, heuristic: true };
    if ((depth || 0) > 2) { out.unknown = true; return out; }
    var data = String((tx && tx.data) || '').trim().toLowerCase();
    if (data && !/^0x[0-9a-f]*$/.test(data)) { out.invalid = true; return out; }

    var value = 0n;
    try {
      var vRaw = String((tx && tx.value) || '0').trim() || '0';
      value = BigInt(vRaw);
    } catch (e) { out.invalid = true; return out; }
    if (value > 0n) {
      out.actions.push(act('txr.a_native',
        'Sends {eth} of the chain’s native coin with this transaction.', { eth: weiToEth(value) }));
      out.flags.push(flag('sends_native', 'info'));
    }

    if (!data || data === '0x') {
      if (value === 0n) out.empty = true;
      return out;
    }
    var sel = data.slice(0, 10), body = data.slice(10);
    var dec = DECODERS[sel];
    if (dec) {
      try { dec(body, out, depth || 0); }
      catch (e) { out.unknown = true; out.flags.push(flag('undecodable', 'info')); }
    } else {
      // Unknown means unknown: not safe, not dangerous — undecoded.
      out.unknown = true;
      out.actions.push(act('txr.a_unknown',
        'Calls function {sel} — not in the known set, so what it does is UNDECODED here. Unknown is not the same as safe.', { sel: sel }));
      out.flags.push(flag('unknown_function', 'info'));
    }
    return out;
  }

  return { decodeTx: decodeTx, UNLIMITED: UNLIMITED, SELECTORS: Object.keys(DECODERS) };
}));
