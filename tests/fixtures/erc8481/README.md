# ERC-8481 test vectors

Copied byte for byte from the merged draft (ethereum/ERCs, PR 2134, merge commit
`c1731e7ab2329d34365f8c2dcf52b7cca5b19caa`), `assets/erc-8481/`. CC0-1.0.

| file here | path in the ERC assets | what it holds |
|---|---|---|
| `coverage.json` | `coverage/coverage.json` | `coverage` and approval-path conformance, report hashes, the EIP-712 digest to sign |
| `normalize.json` | `on-list/normalize.json` | `address.on-list` account normalization (EVM, bech32, base58, other namespaces) |
| `list-hash.json` | `on-list/list-hash.json` | list document normalization and `hash` |
| `match.json` | `on-list/match.json` | matching, `value`, not-read reasons, list conformance |
| `fbi-tradertraitor-2026-10-31.list.json` | `on-list/fbi-tradertraitor-2026-10-31.list.json` | one attested 628-entry list document |

The `on-list` set is credited to the RUNECLAW team in the ERC. `diff.json` (an informative format the ERC does not
define) and the text-injection and Foundry vectors are not used. `tests/test_claw_guard_erc8481_vectors.py` runs
Claw Guard's own implementation against these files; it does not import the ERC's checker.
