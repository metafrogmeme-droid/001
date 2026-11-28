# Claw Guard: ERC-8481 pre-sign risk report

[ERC-8481](https://github.com/ethereum/ERCs/pull/2134) (a draft) has a risk source answer "what does this request
touch, and what did you read about it" in a signed report. The report is bound to the exact request by its digest,
holds typed findings, and has no score, no verdict and no recommended action. The consumer decides.

`bot/claw_guard/erc8481.py` turns the answer of the admin-call check
(`evaluate_admin_calls`, see `docs/CLAW_GUARD_ADMIN_CALLS.md`) into that report. It is off by default, it is
not wired to the order path, the signer or the service, and it holds no key.

## Switch and signer

- `CLAW_GUARD_ERC8481` is off unless it is exactly `1` or `true` (`yes` and `on` stay off).
  While off, `emit_report` returns `None` and builds and signs nothing.
- Signing goes through an injected `ReportSigner`: an `account` (CAIP-10) and
  `sign_digest(digest: bytes) -> bytes` returning the 65-byte `r || s || v`. This module has no key, no key
  derivation and no file or environment read apart from the flag. The test signer is in
  `tests/test_claw_guard_erc8481.py`; its key is derived at run time and exists nowhere else.
- The signer is asked once, for the EIP-712 digest of the finished report. A signature that is not 65 bytes, or does
  not recover to the signer's own account, raises. An unsigned report is never returned.

```python
report = emit_report(request, check_result, source=Source(registry, agent_id), signer=signer, issued_at="…Z")
```

`request` is an ERC-8481 `PresignRequest` with a `calls` subject. `check_result` is the check's answer for the same
request. A result that is not about this request's calls, or a request with a context this source does not read
(payment, texts, tokens, intent), raises `ValueError` instead of being half-reported.

## What the report holds

| member | how it is made |
|---|---|
| `subjectDigest` | `digest({chainId, from, calls, typedData, authorizationList})` from the request (JCS, keccak256). Never taken from the check result. |
| `contextDigest` | `digest(context)`, or 32 zero bytes with no context |
| `entities.addresses` | every call the check visited, at any depth (`target`); the receiver of native value (`recipient`); the recovered signer of each EIP-7702 tuple (`authority`); each Safe the request changes (`account`) |
| `coverage` | a gap for every part the check did not read to the end (below) |
| core address findings | `address.code`, `address.first-seen-block`, `address.prior-transfers` are **not read: unsupported** for every address. Claw Guard reads no chain state, and a missing read is never written as zero. |
| `address.delegation` | not read: unsupported, for each `authority` |
| `account.power-change` | one per change to a Safe: `module-enabled`, `module-disabled`, `owner-added`, `owner-removed` are read from the calldata; `threshold-changed`, `owner-swapped`, `guard-set`, `fallback-handler-set` are counted and not read (their before or after needs chain state). A `delegatecall` made by an account is `code-replaced`. |
| `account.unsigned-actors` | not read: unsupported |
| `com.humanoid-traders.claw-guard.admin-call` | Claw Guard's own kind, under a prefix the project controls: `{kind, signature, newValue}` for each upgrade or admin call, with `param` `{path, kind}` |
| `address.on-list` | only when the request asks for it with `{kind, param: {uri, hash}}`; read against a list the caller passes in, or not read |
| `signature` | EIP-712, domain `Agent Pre-Sign Risk Report` / `1` / the registry's chain id, over `PresignReport(subjectDigest, contextDigest, reportHash)` |

Not in the report, on purpose: the check's decision, its rule ids, its sentences, any score or severity. The user's
rule decides what to do with a finding; the report only states what was read. `conformance_errors` fails a report
that carries `score`, `verdict`, `severity`, `action`, `recommendation`, `decision`, `risk`, `level` or `rating`.

### Coverage gaps

| the check found | gap |
|---|---|
| a batch or wrapper it opened (multicall, Safe `execTransaction` and `multiSend`, ERC-7579 and ERC-7821 execute, account batches) | `unverified-wrapper`, nested `inspected`: its calls were read, but no code was read to show the target runs them as encoded |
| a call whose selector it has no rule for | `unsupported-selector`, nested `not-inspected` |
| depth limit, call limit, data that does not decode | `depth-limit`, `not-inspected`, `undecodable-data`, as the check reported them |
| `typedData` in the request | `unsupported-selector` at `typedData` |
| an EIP-7702 tuple whose signature does not recover | `undecodable-data` at `tx` |

`coverage.status` is `complete` only with no gap. A request with an ERC-20 `approve` in it is therefore always
`partial`: the check has no rule for that selector, and the report says so rather than listing no approval.

## Checked against the draft's vectors

`tests/fixtures/erc8481/` holds the vectors from the merged draft (ethereum/ERCs#2134, merge commit
`c1731e7ab2329d34365f8c2dcf52b7cca5b19caa`, CC0-1.0). `tests/test_claw_guard_erc8481_vectors.py` runs Claw Guard's own
code against them, without importing the ERC's checker:

- `coverage`: the four published reports' subject digests, report hashes and EIP-712 digests, and all 27 coverage and
  approval-path conformance cases;
- `on-list` (the set the ERC credits to the RUNECLAW team): 25 normalization and 10 bech32 / base58 classification
  cases, 15 list-hash cases (including the 628-entry FBI TraderTraitor list), 15 matching cases, 7 list-conformance
  cases and 7 finding scenarios.

The tests also pin JCS key order (UTF-16 code units), and check the EIP-7702 authority recovery against an RLP
encoder written in the test file. In addition, the EIP-712 digest and the authority recovery were compared by hand
against `eth-account` (not a repo dependency).

## Not done

- No registry read (step 3 of the draft's verification: that the signer is the source's on the ERC-8004 registry).
  `verify_report` does steps 1 and 2 for a signer without code; an ERC-1271 contract signer is not verified.
- No `blocks`: nothing is read from a chain, so no finding carries `at`.
- A native-value `recipient` is listed for top-level calls only; value inside a decoded batch is not read, and that
  batch is an `unverified-wrapper` gap.
- ERC-20 and ERC-721 calls, approvals, Permit and Permit2, tokens, payment, texts, simulation and intent entities
  are not produced. A request with payment, texts, tokens or intent context is refused.
- `setModuleGuard`, Zodiac Roles and ERC-7579 `installModule` changes are not decoded by the check, so no
  `power-change` is reported for them; their calls are `unsupported-selector` gaps.
- `prepared-tx-envelope` subjects, and the on-chain anchor.
- `address.on-list` evidence (the `document` evidence entry for the upstream page) is not attached.
- The wording rule holds here too: nothing in this module says a request is safe or a scam.

## Threat model

- A report is only as honest as its coverage. Dropping a gap would make a partly read request look read, so the gaps
  come from what the check actually visited and are re-validated against the request's own paths; the report fails
  `conformance_errors` before it is signed if a gap names a part the request does not have.
- The check result is untrusted input to the emitter: it must list each top-level call with its target, or the report
  is refused as being about another request.
- The signature covers `coverage` and `findings` through `reportHash`. Rewriting a gap changes the hash, so the
  recovered signer changes.
- A signing key is an online secret. This module never sees one. Rotating or revoking the signing key is a registry
  matter (the draft's `signers` list), not done here.
- The report echoes no text from the request, and no calldata.
