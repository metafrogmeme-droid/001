/**
 * Signature X-ray — decode what an off-chain SIGNATURE grants before signing.
 *
 * The transaction X-ray (txray-model.js) reads calldata. Most large drains did
 * not need a transaction from the victim at all: they needed a signature. An
 * EIP-2612 permit, a Permit2 allowance or transfer, or an EIP-7702 delegation
 * is signed in the wallet as a block of typed data, and someone else submits
 * it later. This model reads that block and says what it hands over.
 *
 * Pure, dependency-free, usable from the browser and via require() on the
 * server, like txray-model.js. Stores nothing, reads no chain.
 *
 * Honesty contract:
 * - A message is decoded only when its type is EXACTLY the standard's type.
 *   The EIP-712 hash commits to the type string, so a "Permit" whose fields
 *   differ is not a permit any token contract will accept, and naming it one
 *   would be a guess. It answers UNKNOWN, and unknown is not safe.
 * - Token amounts are RAW UNITS, as in the transaction X-ray.
 * - Times are unix seconds, read against the `now` the caller passes, so the
 *   same input and the same `now` always decode the same way.
 * - Flags are heuristic reads with reasons, never verdicts.
 * - Every action carries {tid, params} beside its English, so the page
 *   renders it in any language and falls back whole, never mixed.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.RCSigRay = factory();
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /** Permit2's one address, the same on every chain it is deployed to. */
  var PERMIT2 = '0x000000000022d473030f116ddee9f6b43ac78ba3';
  var ZERO = '0x0000000000000000000000000000000000000000';
  var UNLIMITED = (1n << 128n); // the transaction X-ray's line
  var U48_MAX = (1n << 48n) - 1n;
  /** A grant or a signature that outlives this many seconds is flagged. */
  var LONG_LIVED_S = 30n * 24n * 3600n;

  function F(name, type) { return { name: name, type: type }; }

  /** The standards' own types. A message whose types differ is not decoded. */
  var STANDARD = {
    Permit2612: [F('owner', 'address'), F('spender', 'address'), F('value', 'uint256'),
      F('nonce', 'uint256'), F('deadline', 'uint256')],
    PermitDai: [F('holder', 'address'), F('spender', 'address'), F('nonce', 'uint256'),
      F('expiry', 'uint256'), F('allowed', 'bool')],
    PermitDetails: [F('token', 'address'), F('amount', 'uint160'),
      F('expiration', 'uint48'), F('nonce', 'uint48')],
    PermitSingle: [F('details', 'PermitDetails'), F('spender', 'address'),
      F('sigDeadline', 'uint256')],
    PermitBatch: [F('details', 'PermitDetails[]'), F('spender', 'address'),
      F('sigDeadline', 'uint256')],
    TokenPermissions: [F('token', 'address'), F('amount', 'uint256')],
    PermitTransferFrom: [F('permitted', 'TokenPermissions'), F('spender', 'address'),
      F('nonce', 'uint256'), F('deadline', 'uint256')],
    PermitBatchTransferFrom: [F('permitted', 'TokenPermissions[]'), F('spender', 'address'),
      F('nonce', 'uint256'), F('deadline', 'uint256')],
  };

  function act(tid, en, params) { return { tid: tid, en: en, params: params || {} }; }
  function flag(id, sev) { return { id: id, sev: sev }; }

  function sameFields(got, want) {
    if (!Array.isArray(got) || got.length !== want.length) return false;
    for (var i = 0; i < want.length; i++) {
      if (!got[i] || got[i].name !== want[i].name || got[i].type !== want[i].type) return false;
    }
    return true;
  }

  /** The first `n` fields match, and the rest is one field named `witness`. */
  function witnessOf(got, want) {
    if (!Array.isArray(got) || got.length !== want.length + 1) return null;
    if (!sameFields(got.slice(0, want.length), want)) return null;
    var w = got[want.length];
    return w && w.name === 'witness' && typeof w.type === 'string' ? w.type : null;
  }

  function big(v) {
    if (typeof v === 'bigint') return v;
    if (typeof v === 'number') {
      // JSON.parse already rounded anything past 2^53; reading it as exact
      // would print a number nobody signed.
      if (!Number.isSafeInteger(v) || v < 0) throw new Error('imprecise');
      return BigInt(v);
    }
    var s = String(v == null ? '' : v).trim();
    if (!/^(0x[0-9a-fA-F]+|[0-9]+)$/.test(s)) throw new Error('not a number');
    return BigInt(s);
  }

  function address(v) {
    var s = String(v == null ? '' : v).trim().toLowerCase();
    if (!/^0x[0-9a-f]{40}$/.test(s)) throw new Error('not an address');
    return s;
  }

  function amountParams(v) {
    return v >= UNLIMITED
      ? { amount: 'UNLIMITED', unlimited: true }
      : { amount: v.toString(), unlimited: false };
  }

  /** A moment as the page shows it: 'NEVER' past uint48, else unix seconds. */
  function moment(v) { return v >= U48_MAX ? 'NEVER' : v.toString(); }

  /** Flags for a deadline or an expiry read against `now`. */
  function timeFlags(out, until, now) {
    if (until < now) out.flags.push(flag('expired', 'info'));
    else if (until - now > LONG_LIVED_S) out.flags.push(flag('long_lived', 'medium'));
  }

  /** `[signer, json]` (eth_signTypedData_v4 params), a JSON string, or the object. */
  function parse(input) {
    var v = input, signer = null;
    if (Array.isArray(v) && v.length === 2) {
      signer = v[0];
      v = v[1];
    }
    if (typeof v === 'string') v = JSON.parse(v);
    if (!v || typeof v !== 'object' || typeof v.primaryType !== 'string'
        || !v.types || typeof v.types !== 'object' || !v.message || typeof v.message !== 'object') {
      throw new Error('not typed data');
    }
    return { td: v, signer: signer };
  }

  function p2Details(d, spender, now, out) {
    var amount = big(d.amount), exp = big(d.expiration);
    var p = amountParams(amount);
    p.token = address(d.token);
    p.spender = spender;
    // Permit2 reads an expiration of 0 as "this block only".
    p.expires = exp === 0n ? 'BLOCK' : moment(exp);
    out.actions.push(act('sgr.a_p2_allow',
      'Lets {spender} move up to {amount} raw units of {token} from you through Permit2, until {expires}.', p));
    if (amount >= UNLIMITED) out.flags.push(flag('unlimited_approval', 'high'));
    if (exp !== 0n) timeFlags(out, exp, now);
  }

  function p2Take(tp, spender, out) {
    var amount = big(tp.amount), p = amountParams(amount);
    p.token = address(tp.token);
    p.spender = spender;
    out.actions.push(act('sgr.a_p2_take',
      'Lets {spender} take up to {amount} raw units of {token} from you, once, with no further signature.', p));
    if (amount >= UNLIMITED) out.flags.push(flag('unlimited_approval', 'high'));
  }

  /** Decoders by primary type. Each returns false when the types are not the standard's. */
  var DECODERS = {
    Permit: function (td, out, now) {
      var t = td.types.Permit, m = td.message, token = address(td.domain.verifyingContract);
      if (sameFields(t, STANDARD.Permit2612)) {
        var v = big(m.value), dl = big(m.deadline), p = amountParams(v);
        p.token = token; p.owner = address(m.owner); p.spender = address(m.spender);
        p.deadline = moment(dl);
        out.actions.push(act('sgr.a_permit',
          'Grants {spender} an allowance of {amount} raw units of {token}. Anyone holding this signature can submit it until {deadline}, and the allowance has no end of its own.', p));
        out.flags.push(flag('gasless_permit', 'medium'));
        if (v >= UNLIMITED) out.flags.push(flag('unlimited_approval', 'high'));
        timeFlags(out, dl, now);
        return true;
      }
      if (sameFields(t, STANDARD.PermitDai)) {
        var on = m.allowed === true || m.allowed === 'true';
        var ex = big(m.expiry);
        var q = { token: token, holder: address(m.holder), spender: address(m.spender),
          deadline: ex === 0n ? 'NEVER' : moment(ex) };
        out.actions.push(on
          ? act('sgr.a_permit_dai_on',
            'Grants {spender} an UNLIMITED allowance of {token}. Anyone holding this signature can submit it until {deadline}.', q)
          : act('sgr.a_permit_dai_off', 'Revokes {spender}’s allowance of {token}.', q));
        out.flags.push(flag('gasless_permit', 'medium'));
        if (on) out.flags.push(flag('unlimited_approval', 'high'));
        // DAI reads an expiry of 0 as "never".
        if (ex === 0n) out.flags.push(flag('long_lived', 'medium'));
        else timeFlags(out, ex, now);
        return true;
      }
      return false;
    },
    PermitSingle: function (td, out, now) {
      if (!sameFields(td.types.PermitSingle, STANDARD.PermitSingle)
          || !sameFields(td.types.PermitDetails, STANDARD.PermitDetails)) return false;
      var m = td.message, spender = address(m.spender);
      p2Details(m.details, spender, now, out);
      timeFlags(out, big(m.sigDeadline), now);
      return true;
    },
    PermitBatch: function (td, out, now) {
      if (!sameFields(td.types.PermitBatch, STANDARD.PermitBatch)
          || !sameFields(td.types.PermitDetails, STANDARD.PermitDetails)) return false;
      var m = td.message, spender = address(m.spender);
      if (!Array.isArray(m.details)) throw new Error('details is not a list');
      out.actions.push(act('sgr.a_p2_batch',
        'A batch of {n} Permit2 allowances, each one below.', { n: m.details.length }));
      for (var i = 0; i < m.details.length && i < 50; i++) p2Details(m.details[i], spender, now, out);
      timeFlags(out, big(m.sigDeadline), now);
      return true;
    },
    PermitTransferFrom: function (td, out, now) {
      return p2Transfer(td, out, now, 'PermitTransferFrom', false);
    },
    PermitBatchTransferFrom: function (td, out, now) {
      return p2Transfer(td, out, now, 'PermitBatchTransferFrom', true);
    },
    PermitWitnessTransferFrom: function (td, out, now) {
      return p2Transfer(td, out, now, 'PermitWitnessTransferFrom', false);
    },
    PermitBatchWitnessTransferFrom: function (td, out, now) {
      return p2Transfer(td, out, now, 'PermitBatchWitnessTransferFrom', true);
    },
  };

  /** Permit2's signature transfers, with or without a witness (an order the spender reads). */
  function p2Transfer(td, out, now, name, batch) {
    var base = batch ? STANDARD.PermitBatchTransferFrom : STANDARD.PermitTransferFrom;
    var t = td.types[name];
    var witness = name.indexOf('Witness') >= 0 ? witnessOf(t, base) : null;
    if (name.indexOf('Witness') >= 0 ? !witness : !sameFields(t, base)) return false;
    if (!sameFields(td.types.TokenPermissions, STANDARD.TokenPermissions)) return false;
    var m = td.message, spender = address(m.spender);
    if (batch) {
      if (!Array.isArray(m.permitted)) throw new Error('permitted is not a list');
      out.actions.push(act('sgr.a_p2_take_batch',
        'A batch of {n} one-time Permit2 transfers, each one below.', { n: m.permitted.length }));
      for (var i = 0; i < m.permitted.length && i < 50; i++) p2Take(m.permitted[i], spender, out);
    } else {
      p2Take(m.permitted, spender, out);
    }
    if (witness) {
      out.actions.push(act('sgr.a_p2_witness',
        'Also signs a {witness} that {spender}’s contract reads alongside the transfer — not decoded here.',
        { witness: witness, spender: spender }));
      out.flags.push(flag('undecoded_witness', 'info'));
    }
    out.flags.push(flag('signature_transfer', 'medium'));
    timeFlags(out, big(m.deadline), now);
    return true;
  }

  /**
   * Decode one EIP-712 signing request.
   *
   * @param input      `[signer, json]`, a JSON string, or the typed-data object
   * @param opts.now   unix seconds to read deadlines against (default: the clock)
   * @param opts.chainId  the chain the wallet is on, if known
   */
  function decodeTypedData(input, opts) {
    var out = { kind: 'typed_data', actions: [], flags: [], unknown: false, heuristic: true };
    var o = opts || {};
    var now = BigInt(Math.floor(o.now == null ? Date.now() / 1000 : o.now));
    var parsed;
    try { parsed = parse(input); } catch (e) { out.invalid = true; return out; }
    var td = parsed.td;
    td.domain = td.domain && typeof td.domain === 'object' ? td.domain : {};
    out.primary_type = td.primaryType;
    out.verifying_contract = null;
    out.chain_id = null;
    try {
      if (td.domain.verifyingContract != null) out.verifying_contract = address(td.domain.verifyingContract);
      if (td.domain.chainId != null) out.chain_id = big(td.domain.chainId).toString();
    } catch (e) {
      out.invalid = true;
      return out;
    }
    if (out.chain_id === null) out.flags.push(flag('no_chain', 'medium'));
    else if (o.chainId != null && String(o.chainId) !== out.chain_id) {
      out.flags.push(flag('chain_mismatch', 'high'));
    }

    var isP2 = /^Permit(Single|Batch|TransferFrom|BatchTransferFrom|WitnessTransferFrom|BatchWitnessTransferFrom)$/
      .test(td.primaryType);
    var dec = Object.prototype.hasOwnProperty.call(DECODERS, td.primaryType) ? DECODERS[td.primaryType] : null;
    var known = false;
    if (dec && out.verifying_contract) {
      try {
        known = dec(td, out, now);
      } catch (e) {
        out.flags.push(flag('undecodable', 'info'));
        known = false;
      }
    }
    if (known && isP2 && out.verifying_contract !== PERMIT2) {
      // Permit2's names on another contract: whatever it does, it is not
      // Permit2, and a look-alike of the drain primitive is itself a signal.
      out.flags.push(flag('permit2_lookalike', 'high'));
    }
    if (!known) {
      out.unknown = true;
      out.actions = [act('sgr.a_unknown',
        'Signs a {type} message for {contract} — not a standard this decodes, so what it grants is UNDECODED here. Unknown is not the same as safe.',
        { type: String(td.primaryType).slice(0, 80), contract: out.verifying_contract || '—' })];
      out.flags = out.flags.filter(function (f) { return f.id === 'no_chain' || f.id === 'chain_mismatch' || f.id === 'undecodable'; });
      out.flags.push(flag('unknown_message', 'info'));
    }
    out.flags = once(out.flags);
    return out;
  }

  /** One flag per id: an allowance's expiry and its signature's deadline can both be long. */
  function once(flags) {
    var seen = {};
    return flags.filter(function (f) {
      if (seen[f.id]) return false;
      seen[f.id] = true;
      return true;
    });
  }

  /**
   * Decode one EIP-7702 authorization: `{chainId, address, nonce}`.
   *
   * A delegation hands the account's code to a contract until the owner signs
   * another one. There is no allowance to cap and no expiry, so every
   * delegation is flagged; one valid on every chain (chainId 0) twice over.
   */
  function decodeAuthorization(input, opts) {
    var out = { kind: 'authorization', actions: [], flags: [], unknown: false, heuristic: true };
    var o = opts || {};
    var a = input;
    try {
      if (typeof a === 'string') a = JSON.parse(a);
      if (!a || typeof a !== 'object') throw new Error('not an authorization');
      var delegate = address(a.address != null ? a.address : a.contractAddress);
      var chain = big(a.chainId != null ? a.chainId : a.chain_id);
      out.chain_id = chain.toString();
      if (delegate === ZERO) {
        out.actions.push(act('sgr.a_7702_clear',
          'Clears this account’s delegation: it goes back to a plain account.', {}));
        return out;
      }
      out.actions.push(act('sgr.a_7702',
        'Hands this account’s code to {delegate}: from then on, that contract decides what the account does, until you sign a new delegation.',
        { delegate: delegate, chain: chain === 0n ? 'ANY' : chain.toString() }));
      out.flags.push(flag('delegation_7702', 'high'));
      if (chain === 0n) out.flags.push(flag('any_chain', 'high'));
      else if (o.chainId != null && String(o.chainId) !== out.chain_id) {
        out.flags.push(flag('chain_mismatch', 'high'));
      }
    } catch (e) {
      out.invalid = true;
      out.actions = [];
      out.flags = [];
    }
    return out;
  }

  return {
    decodeTypedData: decodeTypedData,
    decodeAuthorization: decodeAuthorization,
    PERMIT2: PERMIT2,
    STANDARD: STANDARD,
    PRIMARY_TYPES: Object.keys(DECODERS),
    UNLIMITED: UNLIMITED,
    LONG_LIVED_S: LONG_LIVED_S,
  };
}));
