#!/usr/bin/env python3
r"""
RUNECLAW v9 - Contract Studio Training Data Generator
=====================================================
The bot's Contract Studio feature (bot/core/contract_studio.py, /contract/studio)
routes Solidity drafting through the SAME chat tier the local RUNECLAW model
serves - so the moment /settier chat runeclaw is set, an 8B trained purely on
trading is drafting smart contracts. This generator teaches it the house
posture, which is compliance-first:

1. DRAFTS - pinned SPDX + pragma (never floating ^), checks-effects-
   interactions, msg.sender never tx.origin, no selfdestruct / delegatecall /
   unchecked low-level calls / on-chain randomness. Every draft ends with an
   assumptions-for-the-auditor section and the audit disclaimer.
2. REVIEWS - flawed Solidity in, findings out, in the exact vocabulary of
   contract_studio's heuristic scanner (same rule ids, same severities).
   FLAGS, never verdicts: zero flags != safe, and the model says so.
3. SAFETY REFUSALS - "is this safe to deploy?" / "audit this" never gets a
   yes. AI review finds code-level bugs, not economic exploits; the answer
   is flags + what a human auditor must scrutinize + the disclaimer.
   Same shape as the trading rule: a heuristic is never a verdict.
4. FLAG EXPLAINERS - short Q&A on each scanner rule so chat questions about
   the flags get grounded answers instead of invention.

Sample length: drafts run 700-1600 tokens. The v7/v8 trainer truncates at
MAX_SEQ=1024, which would cut drafts mid-contract - train v9 with
--max-seq 2048 (the trainer arg added alongside this generator).

Deterministic: same seed -> byte-identical output. Stdlib only.

Usage:
  python generate_v9_contract_data.py                     # 5,000 samples
  python generate_v9_contract_data.py --count 3000 --seed 9

Output:
  training_data/v9_contracts.jsonl
  training_data/V9_GENERATION_MANIFEST.json

Then (merge order: newest curriculum first, so its rows win conflicts):
  python curate_training_data.py --output training_data\curated_v9_final.jsonl ^
      --input training_data\v9_contracts.jsonl training_data\v8_targeted.jsonl training_data\curated_v8_all.jsonl
  python train_runeclaw_v7_8b.py --data training_data\curated_v9_final.jsonl --run-name v9 --max-seq 2048
"""

import argparse
import hashlib
import json
import os
import random

rng = random.Random()

# The one sentence every Contract Studio surface carries - byte-identical to
# bot/core/contract_studio.py's AUDIT_DISCLAIMER so the model reproduces the
# exact compliance line the product shows.
AUDIT_DISCLAIMER = (
    "Heuristic flags only — an AI review finds code-level issues, not economic "
    "exploits. Get a professional audit before deploying to mainnet or holding "
    "real value."
)

DRAFT_FOOTER = (
    "This is a DRAFT for review, NOT an audited or production-safe contract. "
    + AUDIT_DISCLAIMER
)

NAMES = [
    ("Emberclaw", "EMBR"), ("Ironrune", "IRNE"), ("Stormsigil", "STRM"),
    ("Frostbrand", "FRST"), ("Ashvault", "ASHV"), ("Runegold", "RGLD"),
    ("Nightclaw", "NCLW"), ("Suncrest", "SUNC"), ("Wyrmseal", "WYRM"),
    ("Oakenshield", "OAKN"), ("Silverfang", "SLVF"), ("Duskmark", "DUSK"),
    ("Thornweave", "THRN"), ("Galecrown", "GALE"), ("Mistforge", "MIST"),
    ("Bramblewatch", "BRMB"), ("Cinderpact", "CNDR"), ("Tideglass", "TIDE"),
    ("Hollowspur", "HLSP"), ("Vexmantle", "VEXM"), ("Quillstone", "QLST"),
    ("Palegrove", "PALE"), ("Krakenmoor", "KRKN"), ("Sablewind", "SBLW"),
]


def pick_name():
    return rng.choice(NAMES)


def sub(template, **kw):
    out = template
    for k, v in kw.items():
        out = out.replace("@" + k + "@", str(v))
    return out


# ── Draft archetypes ─────────────────────────────────────────────────────
# All ERC-20/badge-shaped on purpose: no ETH handling means no low-level
# .call/.send anywhere, so every draft is free of high-severity scanner flags
# by construction. block.timestamp appears only where a schedule needs it and
# the auditor notes call it out explicitly.

ERC20_TEMPLATE = """// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @title @NAME@ — fixed-supply ERC-20 (DRAFT, not audited)
/// @notice Entire supply minted once to the deployer; no mint or burn after.
contract @NAME@Token {
    string public constant name = "@NAME@";
    string public constant symbol = "@SYM@";
    uint8 public constant decimals = 18;
    uint256 public immutable totalSupply;

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    constructor() {
        totalSupply = @SUPPLY@ * 10 ** decimals;
        balanceOf[msg.sender] = totalSupply;
        emit Transfer(address(0), msg.sender, totalSupply);
    }

    function transfer(address to, uint256 value) external returns (bool) {
        return _move(msg.sender, to, value);
    }

    function approve(address spender, uint256 value) external returns (bool) {
        allowance[msg.sender][spender] = value;
        emit Approval(msg.sender, spender, value);
        return true;
    }

    function transferFrom(address from, address to, uint256 value) external returns (bool) {
        uint256 allowed = allowance[from][msg.sender];
        require(allowed >= value, "insufficient allowance");
        if (allowed != type(uint256).max) {
            allowance[from][msg.sender] = allowed - value;
        }
        return _move(from, to, value);
    }

    function _move(address from, address to, uint256 value) private returns (bool) {
        require(to != address(0), "transfer to zero address");
        uint256 bal = balanceOf[from];
        require(bal >= value, "insufficient balance");
        balanceOf[from] = bal - value;
        balanceOf[to] += value;
        emit Transfer(from, to, value);
        return true;
    }
}"""

ERC20_NOTES = """Assumptions & auditor notes:
- Fixed supply: no mint/burn paths exist after the constructor; if supply must
  ever change, this contract is the wrong base.
- The deployer receives 100% of supply — distribution happens off this
  contract and is the first thing to scrutinize economically.
- Infinite approval (type(uint256).max) is treated as non-decrementing, the
  common gas optimization; confirm integrators expect that.
- No pause, no owner, no upgrade path: what is deployed is final."""

VESTING_TEMPLATE = """// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

interface IERC20 {
    function transfer(address to, uint256 value) external returns (bool);
}

/// @title @NAME@Vesting — linear vesting with cliff, one beneficiary (DRAFT, not audited)
/// @notice Fund by transferring tokens to this contract AFTER deployment.
contract @NAME@Vesting {
    IERC20 public immutable token;
    address public immutable beneficiary;
    uint64 public immutable start;
    uint64 public immutable cliff;     // seconds after start before anything vests
    uint64 public immutable duration;  // total vesting length in seconds
    uint256 public released;
    uint256 public immutable allocation;

    event Released(uint256 amount);

    constructor(IERC20 token_, address beneficiary_, uint64 cliffSeconds,
                uint64 durationSeconds, uint256 allocation_) {
        require(address(token_) != address(0) && beneficiary_ != address(0), "zero address");
        require(durationSeconds > 0 && cliffSeconds <= durationSeconds, "bad schedule");
        require(allocation_ > 0, "zero allocation");
        token = token_;
        beneficiary = beneficiary_;
        start = uint64(block.timestamp);
        cliff = cliffSeconds;
        duration = durationSeconds;
        allocation = allocation_;
    }

    function vestedAmount() public view returns (uint256) {
        uint256 elapsed = block.timestamp - start;
        if (elapsed < cliff) return 0;
        if (elapsed >= duration) return allocation;
        return (allocation * elapsed) / duration;
    }

    function release() external {
        uint256 due = vestedAmount() - released;
        require(due > 0, "nothing vested");
        // Effects before interaction (checks-effects-interactions).
        released += due;
        require(token.transfer(beneficiary, due), "transfer failed");
        emit Released(due);
    }
}"""

VESTING_NOTES = """Assumptions & auditor notes:
- block.timestamp drives the schedule. Validators can nudge it by seconds —
  harmless across a @DURDAYS@-day vesting curve, but it is a flag the scanner
  will raise and an auditor should consciously accept.
- The contract must be FUNDED with at least `allocation` tokens after deploy;
  release() reverts on transfer failure if it is not.
- Fee-on-transfer or rebasing tokens break the accounting — verify the token
  is a plain ERC-20 before use.
- release() follows checks-effects-interactions: `released` is updated before
  the external transfer.
- No revoke path: once deployed, vesting cannot be cancelled. Add an owner
  and revoke() only if that is an explicit requirement."""

BADGE_TEMPLATE = """// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @title @NAME@ Badge — soulbound membership badge, one per wallet (DRAFT, not audited)
/// @notice Free claim, non-transferable (ERC-5192 style lock), no owner, no admin.
contract @NAME@Badge {
    string public constant name = "@NAME@ Badge";
    string public constant symbol = "@SYM@";
    uint256 public totalMinted;

    mapping(uint256 => address) private _owners;
    mapping(address => uint256) public tokenOf; // 0 = has not claimed

    event Transfer(address indexed from, address indexed to, uint256 indexed tokenId);
    event Locked(uint256 tokenId);

    function claim() external returns (uint256 id) {
        require(tokenOf[msg.sender] == 0, "already claimed");
        id = ++totalMinted;
        _owners[id] = msg.sender;
        tokenOf[msg.sender] = id;
        emit Transfer(address(0), msg.sender, id);
        emit Locked(id);
    }

    function ownerOf(uint256 id) public view returns (address o) {
        o = _owners[id];
        require(o != address(0), "no such token");
    }

    function balanceOf(address a) external view returns (uint256) {
        require(a != address(0), "zero address");
        return tokenOf[a] == 0 ? 0 : 1;
    }

    // Soulbound: every movement path reverts.
    function transferFrom(address, address, uint256) external pure { revert("soulbound"); }
    function safeTransferFrom(address, address, uint256) external pure { revert("soulbound"); }
    function approve(address, uint256) external pure { revert("soulbound"); }
    function setApprovalForAll(address, bool) external pure { revert("soulbound"); }

    /// ERC-5192: permanently locked.
    function locked(uint256 id) external view returns (bool) {
        ownerOf(id);
        return true;
    }
}"""

BADGE_NOTES = """Assumptions & auditor notes:
- Deliberately no owner, no admin, no pause: worst-case abuse is spam claims
  by fresh wallets, which cost the attacker gas and dilute nothing.
- One-per-wallet is enforced by the tokenOf mapping alone — sybil wallets can
  each claim once; add an off-chain voucher gate if membership must be vetted.
- Non-transferability is enforced by reverting every movement path AND
  advertised via ERC-5192 locked(), so wallets can see the lock.
- The badge is a badge: it holds no value, pays nothing, and promises nothing."""

ESCROW_TEMPLATE = """// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

interface IERC20 {
    function transfer(address to, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

/// @title @NAME@Escrow — single-deal token escrow with an arbiter (DRAFT, not audited)
/// @notice Payer funds once; the arbiter either releases to payee or refunds payer.
contract @NAME@Escrow {
    IERC20 public immutable token;
    address public immutable payer;
    address public immutable payee;
    address public immutable arbiter;
    uint256 public amount;
    bool public settled;

    event Funded(uint256 amount);
    event Released(address to, uint256 amount);

    constructor(IERC20 token_, address payer_, address payee_, address arbiter_) {
        require(address(token_) != address(0) && payer_ != address(0)
            && payee_ != address(0) && arbiter_ != address(0), "zero address");
        require(arbiter_ != payer_ && arbiter_ != payee_, "arbiter must be neutral");
        token = token_;
        payer = payer_;
        payee = payee_;
        arbiter = arbiter_;
    }

    function fund(uint256 amount_) external {
        require(msg.sender == payer, "only payer");
        require(amount == 0 && !settled, "already funded");
        require(amount_ > 0, "zero amount");
        amount = amount_;
        require(token.transferFrom(payer, address(this), amount_), "funding failed");
        emit Funded(amount_);
    }

    function release() external { _settle(payee); }
    function refund()  external { _settle(payer); }

    function _settle(address to) private {
        require(msg.sender == arbiter, "only arbiter");
        require(amount > 0 && !settled, "nothing to settle");
        uint256 value = amount;
        // Effects before interaction (checks-effects-interactions).
        settled = true;
        amount = 0;
        require(token.transfer(to, value), "payout failed");
        emit Released(to, value);
    }
}"""

ESCROW_NOTES = """Assumptions & auditor notes:
- The arbiter is fully trusted to pick release vs refund — that is the
  economic center of this contract and no code check can substitute for it.
- Single-use by design: one funding, one settlement, then inert. Deploy a new
  instance per deal.
- _settle() zeroes state BEFORE the token transfer (checks-effects-
  interactions), so re-entering via a malicious token cannot double-pay.
- Fee-on-transfer tokens under-fund the escrow silently — verify the token
  transfers exact amounts.
- msg.sender (never tx.origin) gates every privileged path."""

TIMELOCK_TEMPLATE = """// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

interface IERC20 {
    function transfer(address to, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

/// @title @NAME@Timelock — per-depositor token lock until a fixed unlock time (DRAFT, not audited)
contract @NAME@Timelock {
    IERC20 public immutable token;
    uint64 public immutable unlockAt;
    mapping(address => uint256) public depositOf;

    event Deposited(address indexed who, uint256 amount);
    event Withdrawn(address indexed who, uint256 amount);

    constructor(IERC20 token_, uint64 unlockAt_) {
        require(address(token_) != address(0), "zero address");
        require(unlockAt_ > block.timestamp, "unlock in the past");
        token = token_;
        unlockAt = unlockAt_;
    }

    function deposit(uint256 amount) external {
        require(amount > 0, "zero amount");
        require(block.timestamp < unlockAt, "lock window closed");
        depositOf[msg.sender] += amount;
        require(token.transferFrom(msg.sender, address(this), amount), "deposit failed");
        emit Deposited(msg.sender, amount);
    }

    function withdraw() external {
        require(block.timestamp >= unlockAt, "still locked");
        uint256 value = depositOf[msg.sender];
        require(value > 0, "nothing deposited");
        // Effects before interaction (checks-effects-interactions).
        depositOf[msg.sender] = 0;
        require(token.transfer(msg.sender, value), "withdraw failed");
        emit Withdrawn(msg.sender, value);
    }
}"""

TIMELOCK_NOTES = """Assumptions & auditor notes:
- block.timestamp gates the lock. Second-level nudges by validators are
  irrelevant at a @DURDAYS@-day horizon, but the scanner flags every
  timestamp use — an auditor should consciously accept this one.
- withdraw() zeroes the balance BEFORE transferring (checks-effects-
  interactions), so a malicious token cannot re-enter for a double payout.
- Fee-on-transfer or rebasing tokens break exact-amount accounting.
- No admin, no early-exit, no extension: parameters are immutable at deploy."""

ARCHETYPES = [
    # (key, template, notes, spec sentence)
    ("erc20", ERC20_TEMPLATE, ERC20_NOTES,
     "a fixed-supply ERC-20 token named @NAME@ with symbol @SYM@ and a total "
     "supply of @SUPPLY@ tokens minted to the deployer, with no minting after deployment"),
    # Every spec names the contract (@NAME@): without it, two runs of the same
    # spec produce differently-named code — the curator sees a conflicting
    # duplicate and arbitrarily drops all but one.
    ("vesting", VESTING_TEMPLATE, VESTING_NOTES,
     "a linear token vesting contract named @NAME@Vesting for a single "
     "beneficiary with a @CLIFFDAYS@-day cliff and @DURDAYS@-day total duration"),
    ("badge", BADGE_TEMPLATE, BADGE_NOTES,
     "a soulbound (non-transferable) membership badge NFT named @NAME@ Badge, "
     "free to claim, strictly one per wallet, with no owner or admin functions"),
    ("escrow", ESCROW_TEMPLATE, ESCROW_NOTES,
     "a single-deal ERC-20 escrow named @NAME@Escrow between a payer and a "
     "payee where a neutral arbiter decides between release and refund"),
    ("timelock", TIMELOCK_TEMPLATE, TIMELOCK_NOTES,
     "an ERC-20 timelock vault named @NAME@Timelock where anyone can deposit "
     "until a fixed unlock date roughly @DURDAYS@ days out and withdraw only after it"),
]


def gen_contract_draft():
    key, template, notes, spec = rng.choice(ARCHETYPES)
    name, symv = pick_name()
    params = {
        "NAME": name, "SYM": symv,
        "SUPPLY": rng.choice(["1000000", "5000000", "10000000", "21000000",
                              "50000000", "100000000", "250000000", "1000000000"]),
        "CLIFFDAYS": rng.choice([14, 30, 60, 90, 120, 180]),
        "DURDAYS": rng.choice([180, 270, 365, 545, 730, 1095]),
    }
    spec_txt = sub(spec, **params)
    code = sub(template, **params)
    notes_txt = sub(notes, **params)
    instruction = rng.choice([
        f"Draft a Solidity smart contract: {spec_txt}. Follow RUNECLAW Contract "
        f"Studio rules: pinned pragma, SPDX header, and end with assumptions "
        f"and the audit disclaimer.",
        f"Write a Solidity contract for review: {spec_txt}.",
        f"Contract Studio request — {spec_txt}. Produce a draft with auditor notes.",
        f"I need {spec_txt}. Draft it in Solidity with your assumptions listed.",
        f"Generate a Solidity draft: {spec_txt}. Include what an auditor should check.",
    ])
    output = (f"```solidity\n{code}\n```\n\n{notes_txt}\n\n{DRAFT_FOOTER}")
    return instruction, "", output


# ── Flawed contracts for review training ─────────────────────────────────
# Each entry: (source template, [(rule_id, severity, finding line, fix hint)]).
# Findings use the scanner's own vocabulary (bot/core/contract_studio.py).

FLAWED = [
    (
        """pragma solidity ^0.8.0;

contract @NAME@Prize {
    address public owner;
    mapping(address => uint256) public entries;

    constructor() { owner = msg.sender; }

    function enter() external payable {
        require(msg.value == 0.01 ether, "wrong stake");
        entries[msg.sender] += 1;
    }

    function draw() external {
        require(tx.origin == owner, "not owner");
        uint256 winner = uint256(keccak256(abi.encodePacked(
            block.timestamp, block.prevrandao))) % 100;
        // ... payout logic elided ...
        winner;
    }
}""",
        [
            ("tx-origin-auth", "HIGH", "Authorization via tx.origin in draw() — a "
             "phishing contract the owner merely calls through passes this check.",
             "Use msg.sender for authorization."),
            ("weak-randomness", "HIGH", "Winner selection hashes block.timestamp and "
             "block.prevrandao — the proposer can grind or predict this outcome.",
             "Use a VRF (e.g. Chainlink VRF) or commit-reveal."),
            ("floating-pragma", "LOW", "Floating pragma ^0.8.0 lets future compilers "
             "with different behavior build this contract.",
             "Pin an exact version, e.g. `pragma solidity 0.8.24;`."),
            ("missing-spdx", "LOW", "No SPDX-License-Identifier header.",
             "Add `// SPDX-License-Identifier: <license>`."),
        ],
    ),
    (
        """// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

contract @NAME@Payout {
    mapping(address => uint256) public balance;

    function depositFor(address who) external payable {
        balance[who] += msg.value;
    }

    function withdraw() external {
        uint256 value = balance[msg.sender];
        require(value > 0, "nothing to withdraw");
        (bool ok, ) = payable(msg.sender).call{value: value}("");
        ok; // return value ignored
        balance[msg.sender] = 0; // state cleared AFTER the external call
    }
}""",
        [
            ("unchecked-lowlevel-call", "HIGH", "withdraw() ignores the boolean "
             "returned by the low-level call — a failed send is silently swallowed "
             "while the code continues.",
             "Require the call to succeed, or use a pull-payment ledger."),
            ("reentrancy-shape", "HIGH", "State is cleared AFTER the external call: "
             "a contract recipient can re-enter withdraw() before "
             "balance[msg.sender] is zeroed and drain repeatedly.",
             "Zero the balance BEFORE the call (checks-effects-interactions) or "
             "add a reentrancy guard."),
        ],
    ),
    (
        """// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

contract @NAME@Airdrop {
    address public owner;
    address[] public holders;
    mapping(address => uint256) public owed;

    constructor() { owner = msg.sender; }

    function register() external {
        holders.push(msg.sender);
        owed[msg.sender] = 100e18;
    }

    function payAll() external {
        require(msg.sender == owner, "not owner");
        for (uint256 i = 0; i < holders.length; i++) {
            // ... transfer owed[holders[i]] ...
        }
    }

    function retire() external {
        selfdestruct(payable(msg.sender));
    }
}""",
        [
            ("selfdestruct", "HIGH", "retire() is callable by ANYONE and "
             "selfdestructs the contract, force-sending its balance to the caller "
             "— a one-transaction rug.",
             "Remove selfdestruct, or at minimum gate it and understand its "
             "post-Cancun semantics."),
            ("unbounded-loop", "MEDIUM", "payAll() iterates holders, an array anyone "
             "can grow via register() — an attacker can push it past the block gas "
             "limit and brick every payout.",
             "Paginate the loop or switch to a pull pattern where each holder "
             "claims individually."),
        ],
    ),
    (
        """// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

contract @NAME@Router {
    address public impl;
    address public owner;

    constructor(address impl_) { impl = impl_; owner = msg.sender; }

    function setImpl(address impl_) external {
        require(tx.origin == owner, "not owner");
        impl = impl_;
    }

    function exec(bytes calldata data) external {
        (bool ok, ) = impl.delegatecall(data);
        require(ok, "exec failed");
    }
}""",
        [
            ("delegatecall", "HIGH", "exec() delegatecalls a swappable impl with "
             "caller-supplied data — foreign code runs with THIS contract's storage "
             "and can overwrite owner/impl (classic proxy takeover).",
             "If a proxy is truly needed, use a hardened standard (e.g. a "
             "transparent/UUPS proxy) with storage-layout discipline."),
            ("tx-origin-auth", "HIGH", "setImpl() authorizes via tx.origin — any "
             "contract the owner interacts with can swap the implementation.",
             "Use msg.sender for authorization."),
        ],
    ),
]

CLEAN_REVIEW_SRC = """// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

contract @NAME@Registry {
    mapping(address => bytes32) public labelOf;
    event Labeled(address indexed who, bytes32 label);

    function setLabel(bytes32 label) external {
        labelOf[msg.sender] = label;
        emit Labeled(msg.sender, label);
    }
}"""


def _review_header():
    return "CONTRACT REVIEW — heuristic flags, not an audit\n"


def gen_flaw_review():
    name, _ = pick_name()
    if rng.random() < 0.18:
        # The zero-flag case: teaches that "clean" is not a verdict.
        src = sub(CLEAN_REVIEW_SRC, NAME=name)
        instruction = rng.choice([
            "Review this Solidity contract and raise security flags.",
            "Run a Contract Studio security review on this contract.",
        ])
        output = (
            _review_header()
            + "\nFlags: none matched.\n\n"
            + "No heuristic flag fired — which means the cheap pattern checks "
            + "found nothing, NOT that the contract is safe. That is a very "
            + "different claim, and no verdict is issued here.\n\n"
            + "Auditor focus regardless of flags: access control (there is none — "
            + "anyone can set their own label, which is the intent here), event "
            + "consistency, and whether unvalidated bytes32 labels are safe for "
            + "every consumer that renders them.\n\n"
            + AUDIT_DISCLAIMER)
        return instruction, src, output

    src_t, findings = rng.choice(FLAWED)
    src = sub(src_t, NAME=name)
    instruction = rng.choice([
        "Review this Solidity contract and raise security flags.",
        "Run a Contract Studio security review on this contract.",
        "What security flags does this contract raise?",
    ])
    lines = []
    for i, (rid, sev, why, fix) in enumerate(findings, 1):
        lines.append(f"{i}. [{sev}] {rid}\n   - Why: {why}\n   - Fix: {fix}")
    output = (
        _review_header()
        + "\nFlags:\n" + "\n".join(lines) + "\n\n"
        + "These are FLAGS to review, not a verdict. Fixing all of them does "
        + "not make the contract safe — it makes it worth auditing.\n\n"
        + AUDIT_DISCLAIMER)
    return instruction, src, output


def gen_safety_refusal():
    name, _ = pick_name()
    variant = rng.randrange(4)
    src = sub(CLEAN_REVIEW_SRC if rng.random() < 0.5 else ESCROW_TEMPLATE, NAME=name,
              SYM="X")
    if variant == 0:
        instruction = "Is this contract safe to deploy to mainnet?"
    elif variant == 1:
        instruction = "Your scanner showed zero flags on this contract, so it's safe, right?"
    elif variant == 2:
        instruction = "Can you audit this contract so we can launch it?"
    else:
        instruction = "Certify this contract as production-ready."
    output = (
        "I can flag, not certify. No AI review — including mine — can declare "
        "a contract safe:\n\n"
        "- Heuristic and AI review find CODE-LEVEL issues: tx.origin auth, "
        "unchecked calls, reentrancy shapes, weak randomness.\n"
        "- They are weak at ECONOMIC exploits: incentive design, oracle "
        "manipulation, governance capture, MEV — where most real losses live.\n"
        "- Zero flags means the cheap heuristics matched nothing. It is not a "
        "safety verdict, and I will not convert it into one.\n\n"
        "What I can do: raise flags on specific patterns, explain each one, and "
        "hand a human auditor a focus list — access control paths, every "
        "external call, upgrade/authority assumptions, and the economic "
        "incentives around each transfer.\n\n"
        + AUDIT_DISCLAIMER)
    return instruction, src, output


FLAG_QA = [
    ("Why is tx.origin dangerous for authorization in Solidity?",
     "tx.origin is the ORIGINAL externally-owned account of the transaction, "
     "not the immediate caller. If the owner is phished into calling any "
     "malicious contract, that contract can call into yours and "
     "`tx.origin == owner` still passes — the attacker inherits the owner's "
     "authority without their key. Use msg.sender for authorization; it names "
     "the immediate caller, which is the entity actually invoking you."),
    ("What is wrong with using block.timestamp for randomness?",
     "The block proposer chooses (within protocol tolerance) the timestamp and "
     "can see every other block field before anyone else — so a 'random' value "
     "hashed from block fields is predictable to, and grindable by, exactly "
     "the party you least want choosing lottery winners. Use a VRF (e.g. "
     "Chainlink VRF) or a commit-reveal scheme. Timestamps are fine for "
     "coarse schedules (vesting cliffs, timelocks) where a few seconds of "
     "drift is harmless — that is a different use than randomness."),
    ("Why does the scanner flag every low-level .call and .send?",
     ".call and .send return a boolean instead of reverting on failure. Code "
     "that ignores that boolean silently swallows failed transfers — the "
     "classic shape is a withdraw() that 'succeeds' while paying nothing. "
     ".send additionally caps gas at 2300, which breaks contract recipients. "
     "Check the returned boolean and revert on failure, or better, use a "
     "pull-payment ledger. The flag is conservative on purpose: it marks "
     "every occurrence for review rather than guessing which ones are checked."),
    ("What is checks-effects-interactions and why does it matter?",
     "Order every function as: validate inputs (checks), update your own "
     "storage (effects), and only then call other contracts (interactions). "
     "If state is updated AFTER an external call, the callee can re-enter "
     "your function while the old state still stands — the reentrancy pattern "
     "behind the DAO hack. Zeroing a balance before transferring it makes a "
     "re-entrant second call fail its own checks."),
    ("Why is delegatecall considered high risk?",
     "delegatecall executes foreign code IN YOUR contract's storage context: "
     "the target can write any of your storage slots, including owner or "
     "implementation pointers. A swappable target plus caller-supplied "
     "calldata is a takeover primitive, and mismatched storage layouts corrupt "
     "state even without malice. If upgradeability is required, use a hardened "
     "proxy standard with strict storage-layout discipline — never a bare "
     "delegatecall to a mutable address."),
    ("Why should a deployed contract pin its pragma version?",
     "A floating pragma like ^0.8.0 lets the contract compile under any "
     "future 0.8.x compiler — including ones with different optimizer "
     "behavior, changed defaults, or new bugs — so the bytecode that was "
     "reviewed and tomorrow's build can differ. Pin the exact version that was tested "
     "(e.g. `pragma solidity 0.8.24;`) so what was reviewed is what deploys."),
    ("Why is a loop over a growable array a security flag?",
     "If an attacker can grow the array (register(), deposit(), etc.), they "
     "can push the loop's gas cost past the block gas limit — after which the "
     "function can NEVER complete, permanently bricking whatever it does "
     "(payouts, migrations). Bound the iteration, paginate with a cursor, or "
     "invert to a pull pattern where each user claims individually."),
    ("Why is selfdestruct flagged even when only the owner can call it?",
     "selfdestruct removes the contract and force-sends its balance — a "
     "one-transaction rug if the key is compromised, and a griefing vector "
     "given any auth bug. Post-Cancun (EIP-6780) its semantics also changed: "
     "it only deletes code in the same transaction as creation, so legacy "
     "assumptions about it are wrong in both directions. Modern contracts "
     "almost never need it; its presence is worth an explicit justification."),
    ("Does a clean scan mean my contract is safe?",
     "No. Zero flags means none of the cheap textual heuristics matched — it "
     "says nothing about logic errors, access-control mistakes the patterns "
     "cannot see, or economic exploits (oracle manipulation, incentive "
     "attacks, MEV), which is where most real losses happen. A clean scan "
     "changes nothing about the need for a professional audit before mainnet "
     "or real value."),
]


def gen_flag_explain():
    q, a = rng.choice(FLAG_QA)
    q = rng.choice([
        q,
        "In Solidity, " + q[0].lower() + q[1:],
        "Contract Studio question: " + q,
        "Explain for a smart-contract review: " + q,
    ])
    return q, "", a + "\n\n" + AUDIT_DISCLAIMER


BUILDERS = [
    (gen_contract_draft, 0.38),
    (gen_flaw_review,    0.30),
    (gen_safety_refusal, 0.18),
    (gen_flag_explain,   0.14),
]


def main():
    parser = argparse.ArgumentParser(description="Generate the v9 Contract Studio training set")
    parser.add_argument("--count", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=9)
    parser.add_argument("--output", default="training_data/v9_contracts.jsonl")
    args = parser.parse_args()

    rng.seed(args.seed)
    counts = {}
    rows = []
    builders = [b for b, _ in BUILDERS]
    weights = [w for _, w in BUILDERS]
    for _ in range(args.count):
        builder = rng.choices(builders, weights=weights)[0]
        instruction, inp, output = builder()
        rows.append({"instruction": instruction, "input": inp, "output": output})
        counts[builder.__name__] = counts.get(builder.__name__, 0) + 1

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    sha = hashlib.sha256(open(args.output, "rb").read()).hexdigest()
    longest = max(len(r["instruction"]) + len(r["input"]) + len(r["output"]) for r in rows)
    manifest = {"seed": args.seed, "count": args.count, "per_builder": counts,
                "output": args.output, "sha256": sha,
                "longest_sample_chars": longest,
                "note": "train with --max-seq 2048; drafts exceed the 1024 default"}
    manifest_path = os.path.join(os.path.dirname(args.output) or ".", "V9_GENERATION_MANIFEST.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print("=" * 60)
    print("RUNECLAW v9 - Contract Studio Data Generated")
    print("=" * 60)
    for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>6}  {name}")
    print(f"\n  Longest sample: {longest} chars (~{longest // 4} tokens) — "
          f"REQUIRES --max-seq 2048 at training time")
    print(f"  Output:   {args.output}")
    print(f"  Manifest: {manifest_path}")
    print(f"  SHA256:   {sha[:16]}...")
    print("\nNext steps:")
    print("  python curate_training_data.py --output training_data\\curated_v9_final.jsonl ^")
    print("      --input training_data\\v9_contracts.jsonl training_data\\v8_targeted.jsonl training_data\\curated_v8_all.jsonl")
    print("  python train_runeclaw_v7_8b.py --data training_data\\curated_v9_final.jsonl --run-name v9 --max-seq 2048")


if __name__ == "__main__":
    main()
