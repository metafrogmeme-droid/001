# Conservation-Gated Collateral

A testnet brake for bridged collateral. `brake()` reads destination-chain facts
before it accounts a bond. A reading with no violation, or a reading that
fails, reverts and the bond is not kept. New borrows and new collateral
deposits stop for `W_brake`. Repay, withdraw and liquidation keep working. The
liquidation price does not move until a violation root is finalized.

An unfinalized root, posted by `W_prov`, can only extend the brake. The bond is
slashed only when a finalized root shows no violation. Half is paid to the
caller who brings that root and half to the curator pool. Lifting the brake
pays nobody. The bond is returned in full when no root is final by
`W_final_max`, when the proof system is paused or retired, when the dispute
game type changes during the claim, or when a watcher vote never resolves.

## Windows

The four timers are per chain, in `chains.json`, and the same numbers are what
`CGCChains.windows` returns. A curator may lengthen them for new claims and
cannot set one shorter than that chain's row. `W_final_max` is strictly after
`W_final`. The stored values are the 23 Oct 2026 spec answer. Where a row's
`basis` says a live challenge contract was not read, that number is an open
question, not a measurement.

| chain | W_brake / W_prov | W_final | W_final_max | proof |
|---|---|---|---|---|
| Ethereum Sepolia | 6h / 1h | 2h | 24h | posted root |
| OP Sepolia, Ink Sepolia | 6h / 2h | 7d+6h | 18d | dispute game, game type snapshotted |
| Unichain Sepolia | 6h / 2h | 7d+6h | 18d | `DisputeGameFactory`, game type 9 (`SUPER_CANNON_KONA`) |
| Base Sepolia | 6h / 24h | 5d+6h | 8d | dispute game, game type snapshotted |
| Arbitrum Sepolia | 6h / 2h | 6d8h+6h | 15d | posted root |
| BSC Chapel, Fuji, Monad testnet, HyperEVM testnet, Plasma testnet | 6h / 6h | 6h | 24h | watcher vote only |
| Solana devnet | same 6h row | | | config only, no program in v0 |

Mantle is not in that answer, so it is not in the allowlist. A constructor on
any other chain id, including each row's mainnet id, reverts.

Watcher votes can confirm a violation or refund the bond. They cannot slash.
There is no hash-based proof verifier in this tree. Groth16, Plonk, a bridge
verifier, and an unimplemented hash proof are refused. The dispute-game path
checks game type, root equality and a defender win. It does not run the fault
proof program.

## gwrsETH

`CGCGwrsETH` is a testnet receipt. The underlying address is a constructor
argument. No mainnet token address is stored here, and there is no deploy
script. Mint is limited to the curator and to `CGCMorphoRoute`. Both share the
inflow cap with the Euler hook, so the wrapper does not keep a second counter.
Burn returns the underlying and still works while the brake is on.

`CGCMorphoRoute` is the testnet path that can mint the receipt and refuse a new
borrow while the brake is on. It is not a Morpho Blue market and it does not
call a Morpho factory. Its constructor reverts on a mainnet chain id, same as
the receipt.

Euler's liquidation bit is 11. Bit 12 is the flash loan and stays blocked while
the brake is on. The vault call selector was not checked against a deployment.

## HyperEVM scope

`fixtures/hyperevm.json` (23 Oct 2026) is the asset list for that chain.
HyperLend's only OFT collateral in scope is sUSDe. USDe's collateral factor is
a measured 0. USR is frozen. USD₮0 is accepted and is not itself an OFT.
Morpho/Felix on HyperEVM is scoped to cmETH. Those names are not token
contracts in this repository.

## Tests

`npm test` compiles with solc and runs the bytecode on an in-process EVM. This
repository does not run Foundry, Rust, Anvil or a mainnet fork for this module.
The server flag `CGC_ENABLED` is not read by these contracts. It defaults off
in the website, the surface is admin-only, and there is no public dashboard.
The server does not hold a mainnet key and does not sign.
