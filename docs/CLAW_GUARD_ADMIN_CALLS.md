# Claw Guard: upgrade and admin-key calls

One call can hand a contract, a multisig or an account to someone else: a
proxy upgrade, a new admin, a new owner, a new signer, a delegation of the
signing account. The Atoshi drain (27 Nov 2026) was an EIP-1967 proxy upgrade
signed with a phished deployer key. `bot/claw_guard/admin_calls.py` finds these
calls in a prepared EVM request and answers with the three Claw Guard
sentences. It is a library. It is not wired to the order path, the service door
or any route, so nothing calls it until a caller is added, and it signs nothing.

## The three answers

| decision | sentence |
|---|---|
| ALLOW | `Allowed: within your limits` |
| CONFIRM | `Needs your OK: <reason>` |
| BLOCK | `Blocked by your rule: <reason>` |

Block beats confirm. The module never says a request is safe or a scam: a
reason names the call, where it sits and which rule applied.

## What is an admin call

| kind | rule id | calls |
|---|---|---|
| `proxy-upgrade` | `UPG-1` | `upgradeTo(address)`, `upgradeToAndCall(address,bytes)` (proxy, UUPS and beacon share these selectors), proxy-admin `upgrade(address,address)` and `upgradeAndCall(address,address,bytes)`, `diamondCut` |
| `set-implementation` | `IMPL-1` | `setImplementation(address)`, `upgrade(address)` |
| `admin-change` | `ADM-1` | `changeAdmin(address)`, `changeProxyAdmin(address,address)`, `setAdmin(address)` |
| `ownership-transfer` | `OWN-1` | `transferOwnership(address)`, `acceptOwnership()`, `setOwner(address)` |
| `ownership-renounce` | `OWN-2` | `renounceOwnership()` |
| `admin-role-grant` | `OWN-3` | `grantRole(bytes32,address)` when the role is `0x00` (`DEFAULT_ADMIN_ROLE`) |
| `multisig-owner-change` | `MSIG-1` | `addOwnerWithThreshold`, `removeOwner`, `swapOwner` |
| `multisig-threshold-change` | `MSIG-2` | `changeThreshold` |
| `multisig-power-change` | `MSIG-3` | `enableModule`, `disableModule`, `setGuard`, `setFallbackHandler` |
| `delegatecall` | `DLG-1` | a delegatecall that is not to a published MultiSend |
| `eip7702-delegation` | `DEL-1` | an authorization-list tuple whose address is not zero |

An unreadable part is `READ-1`. A request that is not a list of calls is `REQ-1`.

## The answer carries what it did not understand

Besides `decision`, `rules`, `text`, `order_hash` and `ts` (the shape of every Claw Guard answer), the
answer lists `findings` (each with its kind, path, target, signature and `new_value`), `gaps`
(recognised parts that did not decode), `wrappers` (the paths of the batch wrappers it opened; their
inner calls were read as encoded, not verified against the target's code) and `undecoded` (calls with
calldata this module has no selector for), and `calls` (every call it visited, with its target). A
`delegatecall` finding also names the `account` whose context would run the delegate's code. `wrappers`,
`undecoded` and `calls` never change the decision. They exist so that a
caller that reports on the check, such as an ERC-8481 report, can say what was not read instead of
implying that nothing was there.

A call's `to` is a plain `0x` address or `eip155:<chain>:0x...`, as an ERC-8481 request writes it.

Selectors come from the signatures in `SIGNATURES` through the repo's keccak.
The tests pin the well-known ones to their published hex.

## Where it looks

A call is read where it sits, to `MAX_DEPTH` (4) levels and `MAX_CALLS` (128)
calls in all:

- `multicall(bytes[])`, `multicall(uint256,bytes[])`, `multicall(bytes32,bytes[])`
- Multicall3 `aggregate`, `tryAggregate`, `aggregate3`, `aggregate3Value`
- ERC-7579 and ERC-7821 `execute(bytes32,bytes)`: batch, single and delegatecall modes
- account `execute` and the three `executeBatch` forms (ERC-4337 and EIP-7702 accounts)
- multisig `execTransaction`, `execTransactionFromModule` and `multiSend`
- the authorization list of an EIP-7702 transaction, tuple by tuple

A multisig `execTransaction` with operation 1 (delegatecall) to one of the four
published MultiSend deployments is read through. Any other delegatecall is a
finding, and its calldata is not read: a contract that is not MultiSend may do
anything with the bytes it is given.

Paths follow the ERC-8481 style: `tx.calls[1].inner[0].inner[2]`,
`tx.authorizationList[0]`.

## Your rule

```json
{
  "admin_calls": {
    "allow":   [{"target": "0x...", "kinds": ["proxy-upgrade"], "new_value": "0x..."}],
    "confirm": [{"target": "0x...", "kinds": ["admin-change"]}],
    "default": "block",
    "unreadable": "confirm"
  }
}
```

- Nothing is allowed unless an `allow` entry names the **target** and the
  **kinds**. An entry without a valid target or without kinds is dropped, so a
  half-written rule can only block more, never less. `new_value` pins the
  address that takes the power (the new implementation, owner or admin).
- A `confirm` entry that matches asks instead of blocking, and wins over an
  `allow` entry for the same call.
- `default` is `block` or `confirm`. Any other value blocks.
- An unreadable part (a recognised wrapper that does not decode, the depth
  limit, too many calls, a malformed authorization tuple) asks by default and
  can be set to block. No rule allows it: unreadable is not zero.
- For `delegatecall` and `eip7702-delegation` the target is the contract that
  runs (the delegate).

## What it does not do

- It reads calldata only. It does not know what a contract is, so a selector it
  has no entry for is not an admin call, and a wrapper it has no decoder for
  is not opened. `Allowed: within your limits` means no admin call was found
  in what it decodes, not that the request does nothing else.
- Proxy, UUPS and beacon upgrades share selectors, so they read the same.
- `to` that is absent (a contract creation) is not read.
- It does not recover the signer of an authorization tuple, so it cannot say
  whose account a delegation changes.
- The `/guard` page's transaction X-ray (`app/public/js/txray-model.js`) is a
  separate JavaScript decoder with its own verdict words. It does not call this
  module. Bringing the same table to it is a follow-up.
- Wiring: `bot/claw_guard/evaluate.py` still answers any `evmCalls` action with
  `DENY`, and `signer.submit_intent` still signs nothing for it. A caller that
  wants this verdict calls `evaluate_admin_calls(request, rules, now=...)`.

## Threat model

| threat | handled by |
|---|---|
| A phished deployer key signs an upgrade of a live proxy | `UPG-1` blocks it unless your rule names that proxy and (optionally) that implementation |
| The upgrade is hidden in a multicall, a batch or a multisig transaction | the wrappers above are opened to four levels, paths name the call |
| The wrapper is too deep or too big to read | a gap, answered with `Needs your OK` (or block), never `Allowed` |
| A multisig is told to delegatecall into a contract that rewrites its own master copy | `DLG-1` blocks any delegatecall that is not to a published MultiSend |
| A look-alike contract that "is" MultiSend | only the four published addresses are read through |
| The account is delegated by EIP-7702 to a sweeper | `DEL-1` blocks any non-zero delegate that your rule does not name |
| A half-written or hostile rule opens the gate | invalid entries are dropped; a default other than `confirm` blocks; unreadable is never allowed |
| Oversized or malformed requests | refused as `REQ-1` before any decoding; per-call and total limits |

Residual: a malicious wrapper contract whose selector this module does not
know, and a malicious implementation behind a call the user's own rule allows.
