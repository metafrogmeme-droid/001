# XNAV contracts

HyperEVM testnet wrapper for an exit-adjusted NAV feed, a swing vault,
and a per-coin mandate. Chain id 998 in tests. Mainnet chain ids revert
in the constructor. No deploy script and no key.

The narrative, the placeholder file, and the off-chain indexer live in
`docs/xnav/README.md` and `bot/xnav/`. Compile and run the in-process
EVM tests with `npm test` from this directory.
