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

### 2.3 · `v: 4` — a dated call in one of four categories (and `v: 3` before it)

```json
{"v":4,"kind":"duel_pick","round_id":0,"day":"YYYY-MM-DD","category":"direction",
 "symbol":"…","symbol_b":null,"threshold_pct":null,
 "handle":null,"pick":"…","entry_price":0,"entry_price_b":null,
 "resolves_at":"2026-08-16T12:00:00.000Z","picked_at":"2026-08-16T12:00:00.000Z"}
```

`category` is the round's kind (`direction`, `backfade`, `range`, `mover`,
`sports`); `symbol_b` and `entry_price_b` are a mover's second leg and the
price it was snapped at in the same read, `threshold_pct` the band a range
round was played under. Each is `null` where the category has no such thing
(a football call, settled on a score, has no `entry_price` either; its
`symbol` is the feed's event key, `FD:<id>`), so a receipt for
"BTC or ETH, which moves more" commits to both prices it was made at, and a
later change to the band table cannot rescore a round. Carries the horizon
(`resolves_at`) as a sealed fact, so the window a call was made for cannot be
widened afterwards to make it look right.

Receipts sealed before 2026-10-19 are `v: 3`, which carried none of the
category fields. Its order is frozen:

```json
{"v":3,"kind":"duel_pick","round_id":0,"day":"YYYY-MM-DD","symbol":"…",
 "handle":null,"pick":"…","entry_price":0,
 "resolves_at":"2026-08-16T12:00:00.000Z","picked_at":"2026-08-16T12:00:00.000Z"}
```

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

### 2.5 · `v: 6` — a dealt Daily Duel round, sealed before anyone calls it

```json
{"v":6,"kind":"duel_round","day":"YYYY-MM-DD","idx":0,"category":"direction",
 "symbol":"…","symbol_b":null,"threshold_pct":null,"agent_direction":"long",
 "match":null,"nonce":"0123456789abcdef0123456789abcdef","dealt_at":"2026-10-19T00:00:05.000Z"}
```

A commitment. The classic round's rule is that the agent's own call is hidden
until the player commits; this receipt makes that checkable instead of
promised. The round is sealed as dealt, **stance included**, and the seal is on
the card from the first read. The payload stays closed while the round is
open to calls: it opens for a signed-in player who has called that round, and
for everyone once the round's UTC day has ended. `sha256(payload) == seal` over
the opened payload proves the stance was fixed before the call, not after.

`nonce` is thirty-two random hex characters (128 bits): a three-way stance
behind a bare hash would be a three-guess secret. A round dealt before
2026-10-21 carries sixteen (64 bits); the check is the same. `match` is the football round's event
(`{event_id, home, away, kickoff}`) and `null` elsewhere; a round that hides
nothing (range, mover, back/fade, football) opens at once, nonce and all. A
round dealt before 2026-10-19 has no seal; its receipt does not exist rather
than being a blank one.

### 2.6 · `v: 7` — a Daily Duel settlement, a receipt of its own

```json
{"v":7,"kind":"duel_settle","round_id":0,"call_seal":"<64 hex>","round_seal":"<64 hex>",
 "category":"direction","pick":"long","settle_state":"settled",
 "settle_price":0,"settle_price_b":null,"outcome":"up","result":"correct","marks":1,
 "settled_at":"2026-10-20T12:00:00.000Z",
 "second_source":{"provider":"CoinGecko","price":0,"source_at":"2026-10-20T11:55:00.000Z"},
 "second_source_b":null,"disputed":false}
```

Rule 2 keeps a **call** receipt free of outcome fields; an outcome gets a
receipt of its own, minted when the settlement is written, naming the call seal
it settles and the round seal the call was made under. A reader then holds a
chain, round → call → settlement, each hash a leaf in its day's root (§4).
`settle_price` / `settle_price_b` carry the closes (a mover's two legs) or a
match's final score (home, away); `outcome` is the market's answer in the
category's words (`up` `down` `flat`, `break` `hold`, `first` `second` `tie`,
`home` `draw` `away`) and `result` the verdict the public rules give the call
(`correct` `wrong` `push` `unresolved`), `marks` the count it earned.

An **unresolved** settlement is sealed too, with `settle_state: "unresolved"`,
both prices `null`, `outcome: null`, `result: "unresolved"` and `marks: 0`. "We
could not price it" is a dated fact, and the receipt a later rewrite would
most like to lose.

**A second reading, since 2026-10-20.** Three fields ride inside the hashed
bytes after `settled_at`: `second_source` and `second_source_b` (`{provider,
price, source_at}` for the same instant from a second price provider, or
`null` when none was read; the `_b` leg only on a mover) and `disputed`, which
is three-valued: `true` when a read leg disagreed with the settle price past
the operator's tolerance (`DUEL_SETTLE_TOLERANCE_PCT`, default 0.5%), `false`
when every leg was read and agreed, `null` when the settlement was not fully
checked. The venue's close in `settle_price` remains the one price the
verdict is given on; a disputed settlement keeps its `result` and says so on
its surfaces. A receipt minted before this date carries none of the three,
and verifies as it did. A match (`category: "sports"`) is never second-read.

### 2.7 · `v: 8` — a Memeclaw launch, sealed when the chain has it

```json
{"v":8,"kind":"memeclaw_launch","chain_id":6281971,
 "factory":"0x<40 hex>","token":"0x<40 hex>","creator":"0x<40 hex>",
 "name":"Much Claw","symbol":"CLAW","description":"",
 "terms_hash":"0x<64 hex>","tx":"0x<64 hex>","block_number":0,
 "launched_at":"2026-10-21T12:00:00.000Z","creator_bought":"0"}
```

A launch on the Memeclaw testnet launchpad (`docs/memeclaw/MEMECLAW_PLAN.md`).
Before the creator's wallet is asked, the page writes the terms as canonical
JSON (`{"v":1,"kind":"memeclaw_terms","chain_id","factory","creator","name",
"symbol","description"}`, in that order) and passes their SHA-256 to the
factory's `launch`, which records it in its `Launched` event. So the chain
itself dates the terms, at `block_number`, and this seal ties the website's
copy of them to that block. `launched_at` is the block's time, not ours.
`creator_bought` is the coin, in base units as a decimal string, that the
creator bought in the launch transaction (`"0"` for none). The seal is minted
only after the website has read the receipt from the chain, never from what a
browser reported. A coin launched straight at the contract, without the page,
has no terms on our side and no seal.

### 2.8 · `v: 9` — the Daily Duel's chips, on the round, the call and the settlement

Chips are a play-money count the duel hands every player each season (the
calendar month, UTC): 1,000 apiece, never bought, sold, sent or withdrawn,
and reset when the season turns (`app/lib/duel_chips.js`). A player may put
10 to 200 of them on a call, at odds fixed when the round was dealt. Three
receipts carry them. Each is its earlier kind with the chips facts **appended
inside the hashed bytes**, every earlier field in its earlier order.

A round, dealt while chips are on (v6 + `odds`, `odds_source`):

```json
{"v":9,"kind":"duel_round","day":"2026-10-22","idx":0,"category":"direction","symbol":"BTCUSDT",
 "symbol_b":null,"threshold_pct":null,"agent_direction":"long","match":null,
 "nonce":"0123456789abcdef0123456789abcdef","dealt_at":"2026-10-22T00:00:05.000Z",
 "odds":{"long":1.9,"short":1.9},"odds_source":"table-2026-10"}
```

`odds` maps each pick that takes chips to its decimal odds; a pass is never
in it, because a pass cannot be wrong and chips on it could only come back
or win. `odds` is `null` for a round dealt without chips, as a football match
is: the feed carries no prices, and odds nobody measured are not invented.
The odds come from a published table by category and pick, never from the
stance the round hides, so they say nothing about it. The payload opens on
the same rule as v6.

A call that carries chips (v4 + `chips`, `odds`, the decimal they were put
at). A call without chips stays v4. Still free of every outcome field:

```json
{"v":9,"kind":"duel_pick","round_id":1,"day":"2026-10-22","category":"direction","symbol":"BTCUSDT",
 "symbol_b":null,"threshold_pct":null,"handle":null,"pick":"long","entry_price":60000,
 "entry_price_b":null,"resolves_at":"2026-10-23T09:30:00.000Z",
 "picked_at":"2026-10-22T09:30:00.000Z","chips":50,"odds":1.9}
```

The settlement of such a call (v7 + `chips`, `odds_used`, `payout`, `held_until`):

```json
{"v":9,"kind":"duel_settle","round_id":1,"call_seal":"<64 hex>","round_seal":"<64 hex>",
 "category":"direction","pick":"long","settle_state":"settled","settle_price":61200,
 "settle_price_b":null,"outcome":"up","result":"correct","marks":1,
 "settled_at":"2026-10-23T10:00:00.000Z",
 "second_source":{"provider":"CoinGecko","price":61190,"source_at":"2026-10-23T09:55:00.000Z"},
 "second_source_b":null,"disputed":false,"chips":50,"odds_used":1.9,"payout":45,"held_until":null}
```

`payout` is the signed movement the verdict gives the chips: `round(chips ×
(odds_used − 1))` for `correct`, `−chips` for `wrong`, `0` for `push` and
`unresolved`. `held_until` is set when `disputed` is `true`: the verdict and
its marks stand, and the chips are paid as the verdict stands only from that
instant (the settlement plus two hours), so a disputed settlement is never
shown as won or lost before then. It is `null` otherwise. A player's stack is
never stored: it is the season float plus the paid payouts minus the chips
still on the table, recomputed from these receipts' rows on every read.

### 2.9 · `v: 9` — a review of a Daily Duel settlement

A settlement can be wrong in ways its two price reads do not show: a venue
print the index never traded, a match result corrected after the feed read
it. A player may **flag** their own settlement once, within two hours of it
being written; a settlement the second source disputed (§2.6) is queued
without one. The operator then decides it, and the decision is a receipt of
its own. It names the settlement and never rewrites it: the settlement's
bytes, and its seal, are the ones that were sealed.

```json
{"v":9,"kind":"duel_review","round_id":1,"call_seal":"<64 hex>","settle_seal":"<64 hex>",
 "round_seal":"<64 hex>","category":"direction","pick":"long","decision":"corrected",
 "outcome":"up","evidence":"The venue candle printed a wick the index never traded.",
 "reviewed_at":"2026-10-23T11:00:00.000Z"}
```

`decision` is one of three words:

| decision | what it does to the call |
|---|---|
| `stands` | nothing: the settlement's verdict was right |
| `push` | no result: marks cleared, chips returned |
| `corrected` | the market's answer was `outcome` instead; the call is scored again at it, marks and chips alike |

`outcome` is `null` unless the decision is `corrected`, and a correction
must name an outcome the category knows that differs from the one the
settlement gave. `evidence` says what was checked, as one line of plain text
of at most 500 characters; a review without it is refused. A settlement is
reviewed at most once. A flag holds a call's chips as a dispute does, until
the review or the end of the two hours, whichever comes first; a reviewed
settlement's chips are paid at the reviewed verdict. A verifier that reads a
settlement SHOULD look for a review naming it and read the verdict from the
review when there is one.

The review is not trusted because the operator wrote it. It is sealed, folded
into the root of the day it was written (§4), and names the evidence, so a
reader can check what was claimed against the same public prices the
settlement read.

---

## 3 · Addressing

Receipts are served by an opaque key. Two forms are in use:

- `sig:<id>` — an engine signal
- `arena:<18 hex>` — a paper trade, from 9 random bytes
- `round:<seal>`, `duel:<seal>`, `settle:<seal>`, `review:<seal>` — a Daily
  Duel round (§2.5), call (§2.3), settlement (§2.6) and review (§2.9),
  addressed by their own seal: sixty-four hex characters nobody can derive
  from public data. A call's receipt names its round seal and, once there is
  one, its settlement; a settlement's names the call it settles and, once
  there is one, its review; a review's names the settlement it decides.
- `launch:<seal>` — a Memeclaw launch (§2.7). A launch is public on the chain,
  so this key is a convenience, not a capability.

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
- **A seal belongs to the day it was minted**, not to the day of the thing it
  describes: a Daily Duel round sealed as dealt, the call made on it and the
  settlement written for it are three leaves on up to three days, and a
  review of that settlement a fourth. Adding a
  surface never moves a stored root; days completed before the surface was
  sealed keep the root they had.

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

RUNECLAW's own anchors, each sent by a wallet's owner and read back by the
server from two readers that must agree, carry the root in one of these forms:

| chain | where the root is | bytes |
|---|---|---|
| Base, DogeOS testnet | the calldata of a zero-value transaction to the sender's own address | `utf8("RCROOT1:<day>:<root hex>")` |
| Base, Arbitrum One, Scroll, Linea (EAS) | `timestamp(bytes32 root)` on that chain's Ethereum Attestation Service | the root's 32 bytes |
| Dogecoin | one OP_RETURN output | `utf8("RCD1:<day>:")` then the root's 32 bytes |
| Solana | one Memo-program instruction, read at `finalized` commitment | `utf8("RCROOT1:<day>:<root hex>")` |
| Bitcoin | an OpenTimestamps proof of a digest | SHA-256 of one line naming the day and its root |

A day is **multi-anchored** when chains in at least two independence groups
verify it live (a testnet is shown and never counted). That proves when the
root existed, never how good the calls in it were.

### 4.3 · A receipt's proof bundle (`rcproof/1`)

`GET /api/call/<key>/bundle` serves one JSON document holding every fact
needed to check one receipt without asking the publisher again:

| field | what it is |
|---|---|
| `format` | `"rcproof/1"` |
| `key`, `kind`, `seal`, `seal_payload`, `sealed`, `sealed_at` | the receipt exactly as `GET /api/call/<key>` serves the same viewer; `sealed` is true, with a `null` payload, while the call is still sealed to them |
| `merkle`, `merkle_note` | `{ day, root, seal_count, proof, construction }`, or `null` with a note saying why while the day is open |
| `anchors` | each chain the root was recorded on: `chain`, `method` (the row of §4.2's table), `tx`, `explorer`, `independence_group`, `counts_toward_quorum` |
| `bitcoin`, `bitcoin_unread` | the OpenTimestamps `statement`, its `digest` and the proof file (`ots_proof_base64`), or `null`; `bitcoin_unread` is true when the publisher could not read its own proof |

The bundle holds **facts, never a verdict**: no field says "verified". It
discloses nothing the receipt and `/api/roots` do not already serve to the
same viewer, and a live call's bundle is as sealed as its receipt.

`tools/rcverify.js` checks a bundle with Node alone and no code shared with
the publisher. It recomputes the hash (§2.4) and the path (§4.1), checks the
seal was minted on the root's day, reads each anchor from its own chain
through endpoints **it** names, and counts independence groups from **its
own** table: a bundle's labels are never trusted. A chain it cannot read is
unknown, never a pass. Exit `0` every check that ran passed, `1` a check
failed, `3` nothing failed but something could not be checked.

```
node tools/rcverify.js rcproof-<key>.json            # every check, chains included
node tools/rcverify.js rcproof-<key>.json --offline  # the hash and the path only
node tools/rcverify.js rcproof-<key>.json --save-ots # also writes the .ots file for `ots verify`
```

`GET /api/verify/<key>` is the publisher's own live reading of the same
receipt: `hash` (`match` / `mismatch` / `sealed`), `path` (`reaches_root` /
`misses_root` / `none`) and the day's quorum, reported apart and labelled as
the publisher's word. It serves no payload.

---

## 5 · Conformance

An implementation conforms when:

1. `sha256(seal_payload) == seal` for every receipt it serves.
2. A **call** receipt's `seal_payload` contains **no outcome field** at any
   version. An outcome is sealed only in a settlement receipt of its own
   (§2.6), which names the call seal it settles.
3. The served `seal_payload` is the byte-exact string that was hashed.
4. Roots follow §4, and no root is served for an unfinished day.
5. An unreadable receipt is served as an **error**, never as an empty or
   partial one. A receipt-shaped response with a missing or malformed seal is
   worse than no response: a reader who copies a hash that does not verify
   concludes the whole publisher is theatre, including the true parts.
6. Outcomes, when published, are attached to the record — never merged into a
   call's payload. A settlement receipt is an attachment with a seal.

---

## 6 · A note on amounts

RUNECLAW's public receipts carry percent, ratio and count only — never dollar
or virtual-currency amounts. That is a house rule rather than a requirement of
this format, but it is a good one and worth copying: an amount tells a reader
nothing about skill that a percentage does not, and it turns a verification
surface into a disclosure surface.

Duel chips (§2.8) are the one count that looks like an amount, and they are
sealed because the receipt is the proof of what was put before the market
answered. They are not a currency: every player is handed the same float
each season, nobody can buy, sell, send or withdraw a chip, and it is worth
nothing outside the game. The public board shows a player's return on the
float as a percent and how many calls carried chips, and no count of chips.

---

## 7 · Version history

| v | What it seals |
|---|---|
| 1 | Engine signals |
| 2 | Paper trades, sealed at open |
| 3 | Dated directional calls, with a sealed horizon; daily Merkle roots |
| 4 | A signal call with its reasoning; a duel call in one of five categories |
| 5 | A pre-signature scan |
| 6 | A dealt duel round, the agent's stance committed before anyone calls |
| 7 | A duel settlement: prices or score, outcome and verdict, naming the call |
| 8 | A Memeclaw launch: the terms hash the chain recorded, the coin, the creator's buy |
| 9 | Duel chips: a round's dealt odds, a call's chips, a settlement's payout and hold; a settlement's review |

A future version MUST NOT change an existing version's field order — receipts
already sealed must stay verifiable forever. New facts get a new `v`.

---

*Reference implementation: [`app/lib/callseal.js`](../app/lib/callseal.js) and
[`app/lib/sealroot.js`](../app/lib/sealroot.js). Human verification page:
`/provable`. Machine access: `GET /api/call/:key`, `GET /api/call/latest`,
`GET /api/roots`, and the `verify_call` / `get_seal_roots` MCP tools.*
