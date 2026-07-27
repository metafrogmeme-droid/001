# Approvals and delegates — the powers your wallet gives away

Most people think a wallet drain needs your seed phrase. It usually doesn't.
It needs a signature you already made — weeks ago, on a site you trusted,
for a swap that worked fine. This lesson is about that signature: what an
approval actually is, why "unlimited" became the default, and the habit
that closes the door behind you.

## What an approval actually is

An ERC-20 token is a contract holding a table of balances. When a DEX
swaps your USDC, the router contract has to move USDC out of your address —
and the token contract will only allow that if you have *approved* the
router first:

    approve(spender, amount)

That single call writes a standing permission into the token contract:
`spender` may move up to `amount` of your tokens, any time, in any
transaction, without asking again. The permission survives until you
change it. Closing the browser tab does not revoke it. Disconnecting your
wallet from the site does not revoke it. Only another `approve` — with a
smaller amount, usually zero — revokes it.

Solana has the same mechanism with different names: a token account can
carry a *delegate* with a *delegated amount*. Same power, same
persistence, same fix.

## Why "unlimited" became the default

Apps ask for unlimited approvals (a number around 2²⁵⁶) so you only pay
the approval gas once instead of before every trade. That is a real
convenience with a real cost: an unlimited approval means the spender's
*code* — not its intentions — is the only thing between your balance and
zero, forever. If that contract is ever exploited, upgraded maliciously,
or was hostile from the start, the drain does not need anything else from
you. The signature already happened.

A `permit` signature deserves special respect: it moves an approval *by
signature alone*, with no transaction from you at all. A message that
looks like a harmless login can be a permit. Read what you sign.

## The two halves of defense

- **Before signing:** decode what the transaction actually does. An
  approval hiding in a "claim reward" button looks identical to a real
  one at the wallet-popup level — only the calldata tells the truth.
- **After the fact:** review what is already granted, on a schedule.
  Standing permissions accumulate silently; each one is a door you left
  open. The habit that matters is *revoke on exit* — when you stop using
  a protocol, remove its power to spend, the same day.

Both checks are read-only. Neither requires trusting the tool that runs
them, because both can be re-derived from public chain state by anyone.

## The honest limits

No approval checker can list every power you have ever granted — a
bounded registry check covers well-known spenders and says so; a delegate
scan is complete for token delegates but blind to other program
authorities. A clean result narrows the risk; it never erases it. Treat
"clean" as "nothing found where we looked," because that is exactly what
it is.

---

*Education, not investment advice. Mechanisms described are ERC-20
approvals, EIP-2612 permits and SPL delegates as specified — exploits
vary, and a revoked approval cannot undo a drain that already happened.*
