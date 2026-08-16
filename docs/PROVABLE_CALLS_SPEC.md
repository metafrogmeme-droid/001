# Provable Calls — specification v3

A format for committing to a trading decision **before the market answers it**,
such that the commitment can be checked later by anyone, with no trust in the
publisher and no access to their systems.

RUNECLAW implements this. It is written down as a spec because the format is
worth more shared than kept: an agent that seals its calls this way can be
audited by the same tools, and a reader who has learned to check one receipt
can check anybody's. **The advantage is the months of sealed history, not the
format**, so there is nothing to lose by publishing it.

> **Status.** Descriptive, not aspirational. Every rule below is implemented
> and pinned by `app/test/provable_calls_spec.test.js`, which derives the
> payloads from the running code and fails if this document drifts from it.

---

## 1 · What a seal proves, and what it does not

A seal proves that **a set of decision-time facts existed, unchanged, from the
moment of sealing**. That is a narrow claim and the whole value is in its
narrowness.

| It proves | It does not prove |
|---|---|
| These exact facts were fixed at seal time | That the decision was good |
| The record was not edited afterwards | That the publisher sealed *every* call |
| A losing call cannot be quietly deleted from a published day | Any future performance |
| A winning call cannot be back-inserted into a published day | That prices quoted were obtainable |

The second column matters. A seal is a **tamper-evidence** mechanism, not a
performance claim, and an implementation that markets it as the latter is
misusing it.

### The completeness gap, stated plainly

A publisher could seal only their good calls. What closes that gap is the
**daily root** (§4): once a day's root is published and mirrored by a third
party, the *set* is fixed too — a call cannot be added to that day afterwards
without changing a hash somebody else already holds. Roots are therefore the
part that makes seals more than self-certification, and an implementation that
ships seals without roots should say so.

---

## 2 · Sealing

A seal is `sha256(seal_payload)`, hex-encoded lowercase, where `seal_payload`
is a **canonical JSON string** of decision-time facts only.

```
seal = sha256_hex( utf8( seal_payload ) )
```

Three rules, and they are the whole contract:

1. **Key insertion order IS canonical.** The payload is serialised once, in the
   order given below, and stored verbatim. Verifiers **hash the served string**
   and never re-serialise it — there is no re-canonicalisation step, so there is
   nothing to drift.
2. **Decision-time facts only.** No outcome field may appear. Outcomes attach to
   the same record later and cannot alter the seal, which is what makes the seal
   a pre-commitment rather than a summary.
3. **Timestamps are ISO-8601 UTC with milliseconds** (`toISOString()` form).

### 2.1 · `v: 1` — an engine signal

```json
{"v":1,"signal_key":"…","symbol":"…","direction":"…","entry_price":0,
 "stop_loss":0,"take_profit":0,"confidence":0,"pattern":null,"regime":null,
 "created_at":"2026-08-16T12:00:00.000Z"}
```

`pattern` and `regime` are `string | null`. The numeric fields are JSON numbers.

### 2.2 · `v: 2` — a paper trade, sealed at open

```json
{"v":2,"kind":"arena_trade","trade_key":"arena:…","handle":null,"symbol":"…",
 "direction":"…","entry":0,"leverage":0,"tp":null,"sl":null,
 "opened_at":"2026-08-16T12:00:00.000Z"}
```

Sealed at **open**, before the outcome exists. The seal rides onto the
closed-trade record untouched.

`handle` is `string | null` — a null handle is an anonymous receipt, still
verifiable by whoever holds the key. `tp` and `sl` are `number | null`.

**No amount appears.** Not margin, not balance, not PnL — not even virtual ones.
A public receipt carries prices, leverage and times only. (See §6.)

### 2.3 · `v: 3` — a dated directional call

```json
{"v":3,"kind":"duel_pick","round_id":0,"day":"YYYY-MM-DD","symbol":"…",
 "handle":null,"pick":"…","entry_price":0,
 "resolves_at":"2026-08-16T12:00:00.000Z","picked_at":"2026-08-16T12:00:00.000Z"}
```

Carries the horizon (`resolves_at`) as a sealed fact, so the window a call was
made for cannot be widened afterwards to make it look right.

### 2.4 · Verifying one receipt

```python
import hashlib, json, urllib.request
d = json.load(urllib.request.urlopen(f"{HOST}/api/call/{KEY}"))

# 1 · the receipt is intact
assert hashlib.sha256(d["seal_payload"].encode()).hexdigest() == d["seal"]

# 2 · the live record still agrees with what was sealed
p = json.loads(d["seal_payload"])
for k, v in d["current"].items():
    assert p.get(k) == v or k in ("trade_key", "signal_key"), (k, p.get(k), v)
```

Step 2 is the one people skip. A valid hash over a payload that no longer
matches the displayed record means the **display** was rewritten, which is
exactly the failure a receipt exists to surface.

---

## 3 · Addressing

Receipts are served by an opaque key. Two forms are in use:

- `sig:<id>` — an engine signal
- `arena:<18 hex>` — a paper trade, from 9 random bytes

A key is a capability, not a secret: knowing it lets you *verify* a receipt, not
alter one. Implementations MUST NOT make a key guessable from public data if
they intend receipts to be individually shareable.

---

## 4 · Daily roots

Every seal minted on one **UTC day** folds into a single Merkle root.

```
leaves = the day's seals (64-hex), deduplicated, sorted ascending
parent = sha256( utf8( leftHex + rightHex ) )       # hex STRINGS concatenated
odd    = an unpaired last node is promoted unchanged
root   = the single remaining node; a lone leaf is its own root
```

Three details that implementations get wrong:

- **The parent hashes the concatenated hex strings**, not the decoded bytes.
- **Leaves are deduplicated before sorting.** A seal that appears on two
  surfaces (an open position and its later closed record) is one leaf.
- **A day is only rootable once it is over.** A root for the current UTC day
  MUST NOT be served: the set is still growing, and publishing a root over a
  partial day is a commitment nobody can rely on. Asking for today returns
  "the day is still open", never a hash.

### 4.1 · Membership proofs

A proof is an ordered list of `{pos, hash}` where `pos` names where the
**sibling** sits relative to the running hash:

```python
h = receipt["seal"]
for step in receipt["anchor"]["proof"]:
    pair = step["hash"] + h if step["pos"] == "L" else h + step["hash"]
    h = hashlib.sha256(pair.encode()).hexdigest()
assert h == receipt["anchor"]["root"]
```

An odd promoted node contributes **no proof step** — it has no sibling.

`anchor` is `null` for any seal whose day is still open. That is the normal
state for a fresh call, and a widget that renders it as an error is wrong.

### 4.2 · Mirroring

A root becomes independently meaningful the moment a third party copies it
anywhere durable — a gist, a post, a chain memo. From then the publisher cannot
alter that day without contradicting a hash they do not control.

**This is the step that turns self-certification into evidence**, and it costs
the mirrorer nothing but a paste.

---

## 5 · Conformance

An implementation conforms when:

1. `sha256(seal_payload) == seal` for every receipt it serves.
2. `seal_payload` contains **no outcome field** at any version.
3. The served `seal_payload` is the byte-exact string that was hashed.
4. Roots follow §4, and no root is served for an unfinished day.
5. An unreadable receipt is served as an **error**, never as an empty or
   partial one. A receipt-shaped response with a missing or malformed seal is
   worse than no response: a reader who copies a hash that does not verify
   concludes the whole publisher is theatre, including the true parts.
6. Outcomes, when published, are attached to the record — never merged into the
   payload.

---

## 6 · A note on amounts

RUNECLAW's public receipts carry percent, ratio and count only — never dollar
or virtual-currency amounts. That is a house rule rather than a requirement of
this format, but it is a good one and worth copying: an amount tells a reader
nothing about skill that a percentage does not, and it turns a verification
surface into a disclosure surface.

---

## 7 · Version history

| v | What it seals |
|---|---|
| 1 | Engine signals |
| 2 | Paper trades, sealed at open |
| 3 | Dated directional calls, with a sealed horizon; daily Merkle roots |

A future version MUST NOT change an existing version's field order — receipts
already sealed must stay verifiable forever. New facts get a new `v`.

---

*Reference implementation: [`app/lib/callseal.js`](../app/lib/callseal.js) and
[`app/lib/sealroot.js`](../app/lib/sealroot.js). Human verification page:
`/provable`. Machine access: `GET /api/call/:key`, `GET /api/call/latest`,
`GET /api/roots`, and the `verify_call` / `get_seal_roots` MCP tools.*
