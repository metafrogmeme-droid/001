# What RUNECLAW promises about its own numbers

Most trading products ask you to trust their screenshots. This one publishes
the rule its code is held to, and the mechanisms that hold it there — so you
can check the promise instead of believing it.

The rule is one sentence:

> **A failed read is never a zero, and an absent value is never a measurement.**

Everything below follows from it.

---

## What that means on screen

When something cannot be read — an exchange is down, a price feed times out, a
row has no outcome yet — the honest options are to **say so** or to **leave it
out**. Never to substitute a number.

| You will never see | Because it would mean |
|---|---|
| `0.00%` beside a green stripe when the price could not be fetched | "you are break-even", asserted from nothing |
| "No venues found" when the venue list returned a 503 | "we looked and there are none" |
| "Engine live" beside a dead event stream | "it is running", from a socket nobody read |
| A win rate over trades that could not be scored | a measured record, computed from absences |
| `12 (7W/4L)` where the losses are just "everything not a win" | unscorable rows counted as losses |
| A seal-shaped hash on a receipt that could not be loaded | a checkable claim that does not check |

That last one is the sharpest. A visitor who copies a hash, verifies it, and
finds it does not match concludes the **whole publisher** is theatre — including
the parts that were true. So a receipt that cannot be fully loaded renders
nothing at all.

### Colour is a claim

A green accent says "in profit" as loudly as the number does. Unknown gets a
muted one. A direction is never coloured as profit, because a call's outcome
does not exist at the moment it is made — which is the entire point of sealing
it.

### A heuristic is never a verdict

A green health check rules **one** cause out; it does not name the cause.
Scanners report flagged patterns with reasons, and say plainly that a clean
result is not a guarantee.

---

## How it is enforced, not just intended

Knowing a rule has never been enough here — the rule was written down, and then
broken in twenty-plus places across ten pull requests in a single day. What
works is machinery:

- **Structural honesty tests.** Every panel loader must either throw (so the
  caller paints an error) or omit missing sources individually. "Neither" fails
  the suite by construction, not by review.
- **Planted red herrings.** Surface tests assert what a card MUST say, what it
  MUST NOT say, and include a true-but-misleading signal to prove the card does
  not over-read it.
- **Ratchets, not resolutions.** Known-failure lists, an unreachable-module
  inventory and a cache-buster manifest all fail in *both* directions: a new
  entry means something regressed, and a stale entry must be removed in the
  same commit that fixes it.
- **An adversarial pass on the risk engine.** Thirty scenarios — flash crashes,
  liquidity drains, stale data, confidence manipulation, breaker evasion —
  attack the real gate on every change. It is gated at zero failures.

Each of those runs on every change and blocks the build. Applying this page's
own rule to itself: they are pinned by a test that reads this file, so a claim
here cannot outlive the mechanism behind it.

### The one that is a habit, not a gate

**Mutation checking** — a guard is not considered done until the fix has been
reverted and the test watched to fail. A test that cannot fail is worse than no
test, because it converts "nobody checked" into "we check every build".

Nothing enforces this. It is discipline, and discipline is weaker than
machinery; saying otherwise would be the exact substitution this page is
about. It earns its place here because it is the practice that caught the most
— including several tests that looked rigorous and could not fail at all.

---

## What this does *not* promise

Being explicit here is part of the standard.

- **Not that the numbers are good.** Honesty about a figure is unrelated to
  whether the figure is impressive. A truthfully-reported loss is still a loss.
- **Not that nothing will break.** Software breaks. The promise is that when it
  does, it will look broken rather than look fine.
- **Not that every call is sealed.** A publisher could seal selectively. What
  closes that gap is the daily Merkle root, once a third party mirrors it — see
  [the Provable Calls spec](./PROVABLE_CALLS_SPEC.md).
- **Not investment advice**, and past performance never predicts future results.

---

## Check it yourself

The rule is only worth anything if you can test it, so:

- **Verify any single call** — `GET /api/call/:key`, re-derive
  `sha256(seal_payload)` in your own terminal. The format is
  [specified](./PROVABLE_CALLS_SPEC.md); nothing is needed from us but public
  URLs.
- **Mirror a daily root** — `GET /api/roots`. Copy one anywhere public and that
  day is frozen for everyone, including us.
- **Read the enforcement.** The doctrine contributors are held to is
  [`CLAUDE.md`](../CLAUDE.md) in this repository — written for people changing
  the code rather than for people reading this page. It is blunter, it names
  the pull requests where each rule was broken, and it is the same rule.
- **Read the guards.** The files behind every bullet in the section above are
  in `app/test/` and `tests/`, and the mapping from claim to guard is
  `tests/test_engineering_standard_accuracy.py` — which fails if this page
  describes a mechanism that no longer exists.

---

*If you find a surface that breaks this rule, that is a bug and worth reporting
as one. Several of the examples in the table above were found exactly that way.*
