# Sandwich restitution (shadow)

Order-flow providers post a bond against a routing receipt. If a later swap
matches the published detector, the bond would cover the shortfall. v0 runs
that decision in shadow: it records the result and pays nothing.

## What v0 does

- Reads a recorded fixture, or a devnet/testnet source through an injected
  transport. A mainnet label is refused before any read. There is no live
  mainnet shadow mode.
- Signs a routing receipt (ed25519) over the transaction signature, receive
  time, provider, policy hash and slippage cap. The signing key is derived
  for that shadow cluster after the cluster check. No key is loaded from
  disk and nothing is signed for mainnet.
- Closes a receipt batch 30 seconds after receipt. A receipt that is not in
  the batch root cannot be claimed, signature or not.
- Detects a same-pool front and back around a swap whose quoted output is
  above the fill, within a 3-slot window. A missing quote is not a loss.
- Withholds the record when the swap and the front/back share a signer, a
  funding cluster or a deposit cluster; when the gain is not shown accruing
  outside that cluster; when the net over the window is not a loss; when a
  dispute inside the 72-hour window removes the pattern; or when a
  methodology hash is not yet active (7-day timelock).
- Caps the recorded figure by the receipt slippage and the per-swap, per-wallet
  and per-epoch caps. Units stay in the swap's own base units. They are not
  converted.
- Lets a provider become withdrawable 10 days after asking, which is longer
  than the claim window plus the dispute window. Releasing the bond does not
  transfer anything.

The public record is a **public signal**: a slot window, an opaque pool
reference, basis points and the provider status `Bonded`, `Unbonding` or
`Unbonded`. It does not name a wallet, and it does not carry an amount.
Amounts are on the admin view only.

v0 does not mint a claim token, a subrogation claim, or any other token.
No program is deployed. There is no deploy script and no program id.

## Flag

`SRB_ENABLED` is off unless it is `1` or `true`. While it is off the public
site answers 404. An operator (the site's admin plan, or a Telegram admin)
can still open it; the page says `Admin preview. Not public.`

## Layout

| file | role |
|---|---|
| `flag.py` | `flag_on` |
| `shadow.py` | detector, receipt, bond, shadow ledger |
| `report.py` | the recorded window, as JSON for the page |
| `fixtures/demo_window.json` | the window the page shows |
