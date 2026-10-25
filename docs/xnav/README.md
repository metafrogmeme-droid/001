# Exit-adjusted NAV

XNAV ranks a Hyperliquid vault by the cost of leaving it. The figure is a
haircut in basis points of account value. A higher number is a larger exit
haircut. It is not a profit, and it is not a safety score.

The product is off unless `XNAV_ENABLED` is `1` or `true`. It stays
admin-only when the flag is on: `flag_on_for` is the first check, and a
caller who is not the operator still receives 404. While the flag is off,
an operator receives the page and the API with the banner "Admin preview.
Not public." The landing page does not link `/xnav`.

On a mainnet chain id the server does not hold a key and does not sign.
`load_signing_key` refuses a flag that is off, a missing chain, a mainnet
id, a chain outside {998, 31337, 1337}, and `XNAV_SIGN_TESTNET` unset,
and only then reads `XNAV_SIGNING_KEY`. Publication verifies secp256k1.
`ml-dsa`, `falcon` and `dilithium` are rejected by the same scheme check
the fair-value bridge uses.

## One reading of the book

The filled part of an exit walks the resting book with
`app/lib/sar/metrics.js` `walkSide`. The gap is measured from the mid.
A book that cannot fill the size is not zero slippage and not a blank
S(Q): the remainder is priced at the last visible level and capped at
that remainder's notional, and the reason is `unfilled_remainder`. A
walk that would credit the trader (`cost < 0`) is `crossed_book`, which
is unreadable.

Precision is chosen from `full`, then `4`, then `3`, then `2`. The
finest book that covers the size wins. If none covers, the readable book
with the greatest filled size is kept and the reason stays
`unfilled_remainder`. A missing precision key makes the choice
unreadable. The median across precisions is the upper median. If any
precision is unreadable, the median is unreadable. A TWAP is the mean of
a window of exactly N snapshots; a hole is unreadable, not a partial
mean. The off-chain delta cap clamps a move to the previous print plus
or minus the cap. A previous value of null is a first print. A previous
value of zero is a real zero. The chain feed reverts on a jump instead
of clamping.

Stress scales sizes by the placeholder depth ratio and shifts only the
exit side against the trader, keeping the original mid.

The same haircut, already in basis points, is the weight on an RXB
look-through node (`bot/xnav/graph.py` `vault_exit_node`, which calls
`bot/rxb/metrics.py` `solve_graph`). A missing haircut is an unreadable
node. It is not a zero look-through. A haircut above 10_000 caps the
shortfall at one and sets `haircut_above_one`.

## Swing vault

`SwingVault4626` is an ERC-4626-shaped wrapper on HyperEVM. It is not an
OpenZeppelin vault. Shares are an internal balance, and only the owner
redeems.

`totalAssets` is the equity precompile plus swing cash the vault kept.
The precompile call is `abi.encode` of the Hyperliquid vault address,
decoded as `int256`. A failed call, a return shorter than one word, or a
negative value reverts `unreadable`. An account with no code returns
success and empty data, which is the short-return case, not zero assets.
Zero equity is a real reading. The placeholder `equity_scale` is not
applied on chain.

The portion of an epoch's flow still under the threshold is priced at
net asset value. The portion above it pays `min(maxSwing, haircut *
slice / 10000)` on that portion. The haircut that is not paid out stays
in the vault, so the remaining holders keep it. The first deposit, while
supply is zero, is one share per unit and does not read the precompile.
A later redeem that asks for more than the retained cash reverts `idle`.
`app/lib/xnav/swing.js` is the same split. The contract test compares
the two.

A coin with no mandate cap pauses deposits. The cap is not unlimited.
Optional de-risk is a placeholder `CoreWriter` call. The action id comes
from the placeholder file. The server does not sign that call. A failed
call leaves the mandate paused.

The oracle stores a reading only after two distinct secp256k1 signers.
An unset vault reverts `unset`. A stale vault reverts `stale`. Neither
answers zero. Scheme id 0 is the only one `rejectScheme` accepts.

The constructor reverts `testnet only` on chain ids 1, 8453, 42161, 999
and 4663. Chain 998 is the HyperEVM testnet id recorded for this build.

## Placeholders, not measurements

Every figure the source material marked unverified is a key in
`bot/xnav/placeholders.json`, labelled as a placeholder. The contract
receives those numbers as constructor arguments. It does not hard-code
them. The file is the only copy. `app/lib/xnav/config.js` and
`bot/xnav/config.py` both throw when it cannot be read. They do not fill
zeros.

The keys include staleness, the swing cap, the flow threshold, the delta
cap, the TWAP length, the feed bound, the stress depth ratio and shift,
the equity scale (recorded and not applied), the equity and margin
precompile addresses, the CoreWriter address and action id, and the
documented L1 read gas schedule (`base + per_word * (in + out)`). That
gas schedule is a documented formula, not a measurement from this tree.

## API

`GET /api/xnav/rank`, `GET /api/xnav/quote`, `GET /api/xnav/:vault` and
`POST /api/xnav/mcp` each go through `optionalAuth` and then the admin
door. `size` on a quote is a fraction of the vault, greater than 0 and
at most 1. It is not a dollar amount. When `flow` is omitted, `swing_bps`
is null and the reason is `epoch_flow_unreadable`. The haircut is still
returned. A missing snapshot is `not_published` with `ranked: null`. It
is not an empty ranking.

`POST /api/xnav/mcp` answers `tools/list` and `tools/call` for
`xnav_quote` only. That tool is not in the MCP catalogue of thirty-four
tools (`app/routes/mcp.js` and `bot/mcp/server.py`). Those files were
not edited. The lessons index was not edited either: `docs/lessons/INDEX.md`
is generated, and a hand-written line there fails the comparison with
the generator.

## Fixtures

`bot/xnav/fixtures/snapshot_sample.json` is seven vaults from one
resting-book observation on 2026-10-24, stored as basis points only.
Dollar notionals are not in the file. The sample is not a claim about
how many vaults exist. `xmr_top.json` is the top three levels of one
XMR book. `xmr_precision_ladder.json` records whether three precisions
covered that position, and the gap to the last visible price. There is
no `nSigFigs` 4 row in that ladder. `digest_vector.json` is one EIP-712
digest that the Python signer, ethers, and `XNAVOracle.digestFor` agree on.

## Tests and CI

`contracts/xnav` compiles with solc and runs the bytecode on an
in-process EVM (`npm test`). The CI job is `XNAV contracts (solidity)`.
It is not in `scripts/preflight.py` `LOCAL_JOBS`. A local preflight
names it under the jobs only CI runs, which is the same treatment as
the other contract jobs except RXB. The npm advisory ratchet points at
this workspace.

`app/test/xnav.test.js` drives the flag door, the ranking, the quote,
the depth walk against `walkSide`, and the separate MCP surface.
`tests/test_xnav.py` drives the flag, the RXB node, the placeholder
reader, and the signer refusal that happens before the key is read.
