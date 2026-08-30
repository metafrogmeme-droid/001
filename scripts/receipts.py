#!/usr/bin/env python3
"""Every claim $RCLAW makes, beside the command that checks it.

WHY THIS EXISTS

The repo can now verify a lot: that the deployed bytecode matches a rebuild of
the tagged commit, that the presale artifact matches the chain, that a
Proof-of-PnL statement's arithmetic is sound and attributable, that no
unattributed program sits in the money path. Each check lives in a different
place and is invoked differently, so in practice nobody runs them all — and a
verification nobody runs is indistinguishable from one that does not exist.

This collects them into one command and one page. It is not a new check; it is
the index of the existing ones.

THE FAILURE MODE IT IS BUILT AGAINST

A "proof" page is exactly where a project overstates. The temptation is to print
a green tick per row and let absence read as success — a check that could not
run, a network that was down, an artifact that was missing, all rendering
identically to a check that actually passed.

So the states here are deliberately three, not two:

    PASS        the command ran and succeeded
    FAIL        the command ran and did not succeed
    UNVERIFIED  the command did not run to completion

UNVERIFIED is never folded into either of the others, is never coloured green,
and makes the overall exit non-zero unless --allow-unverified is passed. If you
cannot check something, the honest output says so — the whole point of this page
is that a reader can trust it, and a page that quietly upgrades "unknown" to
"fine" is worth less than no page at all.

Usage:
    python3 scripts/receipts.py                  # run everything available
    python3 scripts/receipts.py --offline        # skip anything needing a network
    python3 scripts/receipts.py --markdown       # emit a publishable page
    python3 scripts/receipts.py --allow-unverified   # exit 0 despite gaps

Exit: 0 all PASS (or unverified allowed), 1 any FAIL, 2 unverified without the flag.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

PASS, FAIL, UNVERIFIED = "PASS", "FAIL", "UNVERIFIED"


@dataclass
class Claim:
    """One publishable assertion and the command a stranger runs to test it."""
    key: str
    claim: str          # what is asserted, in plain words
    command: list[str]  # how anyone re-checks it
    why: str            # what a failure would mean
    cwd: Path = REPO
    needs_network: bool = False
    requires: list[str] = field(default_factory=list)  # executables that must exist
    needs_file: str = ""   # repo-relative; absent -> UNVERIFIED, not FAIL
    absent_note: str = ""  # why absence means 'not applicable yet'
    env: dict = field(default_factory=dict)

    @property
    def shell(self) -> str:
        return " ".join(self.command)


CLAIMS: list[Claim] = [
    Claim(
        key="build-reproducible",
        claim="The staking program's bytecode is reproducible, and the mint pin is compiled in",
        command=["python3", "scripts/build_provenance_gate.py"],
        why=("If the build is not reproducible, a published artifact hash proves nothing — an "
             "honest binary and a substituted one are indistinguishable. If the pin stops "
             "changing the bytecode, every release is silently unpinned."),
        requires=["cargo-build-sbf"],
    ),
    Claim(
        key="rust-advisories",
        claim="No NEW RustSec advisory has entered the program's dependency tree",
        command=["python3", "scripts/cargo_audit_gate.py"],
        why="A new advisory in the shipped tree is a supply-chain regression on code that signs for the vault.",
        requires=["cargo-audit"],
    ),
    Claim(
        key="npm-advisories",
        claim="No NEW npm advisory has entered the token tooling",
        command=["npm", "run", "--silent", "audit:gate"],
        why="These packages build and sign the mint, metadata and presale transactions.",
        cwd=REPO / "token",
        requires=["npm"],
    ),
    Claim(
        key="token-tooling-tests",
        claim="The presale tooling's offline invariants hold",
        command=["npm", "test", "--silent"],
        why=("Covers the cluster guard's coverage, keypair permissions, vesting conservation, "
             "LP parity, allowlist serialization and the rollover destination."),
        cwd=REPO / "token",
        requires=["npm"],
    ),
    Claim(
        key="guard-reachability",
        claim="Every security guard is actually CALLED at every site that needs it",
        command=["python3", "scripts/guard_lint.py"],
        why=("Three times in this audit a correct guard existed and call sites never received "
             "it. Unit tests cannot see this: they exercise paths, and an unguarded path is one "
             "nobody wrote a test for."),
    ),
    Claim(
        key="tier-gate-coverage",
        claim="Every paid feature is actually behind the paywall, and no safety command is",
        command=["python3", "-m", "pytest", "-q", "tests/test_tier_gate_coverage.py",
                 "tests/test_token_tier_gate.py"],
        why=("A gated skill dispatched without a gate check is a free feature. A SAFETY command "
             "behind the gate is a user who cannot reach their kill switch."),
    ),
    Claim(
        key="pnl-verifier",
        claim="A Proof-of-PnL statement can be re-derived, and forged identity is rejected",
        command=["python3", "-m", "pytest", "-q", "tests/test_proof_of_pnl_identity.py",
                 "tests/test_proof_of_pnl.py"],
        why=("Without the identity check, anyone can sign a flawless statement of invented "
             "profits with a fresh keypair."),
    ),
    Claim(
        key="program-inventory",
        claim="No unattributed on-chain program executes in the money path",
        command=["npm", "run", "--silent", "programs:inventory"],
        why=("A CPI target is not a package, so no dependency scanner sees it. This is how the "
             "deposit path was found routing through third-party bytecode named nowhere in this repo."),
        cwd=REPO / "token",
        needs_network=True,
        requires=["npm"],
        env={"RPC_URL": os.environ.get("RPC_URL") or "https://api.devnet.solana.com"},
    ),
    Claim(
        key="presale-artifact",
        claim="The published presale artifact matches the chain",
        command=["npm", "run", "--silent", "presale:verify"],
        why=("The artifact records which mint was sold and which buckets hold which allocations. "
             "It is written by the process that sent the transactions, so it records intent."),
        cwd=REPO / "token",
        needs_network=True,
        requires=["npm"],
        env={"RPC_URL": os.environ.get("RPC_URL") or "https://api.devnet.solana.com"},
        needs_file="token/.artifacts/presale.devnet.json",
        absent_note=("no presale has been published yet — only e2e dry runs, whose artifacts "
                     "carry their own names. Nothing to check, which is not the same as a "
                     "check that failed."),
    ),
]


def _have(executable: str) -> bool:
    return shutil.which(executable) is not None


def run_claim(c: Claim, *, offline: bool, timeout: int = 900) -> tuple[str, str]:
    """Return (state, detail). Never raises — a crashed check is UNVERIFIED."""
    missing = [r for r in c.requires if not _have(r)]
    if missing:
        return UNVERIFIED, f"not installed here: {', '.join(missing)}"
    if offline and c.needs_network:
        return UNVERIFIED, "skipped (--offline) — needs a live RPC"
    if not c.cwd.exists():
        return UNVERIFIED, f"{c.cwd} does not exist"
    # "Nothing has been published yet" is not "the published thing is wrong".
    # Reporting it as FAIL would overstate in the opposite direction from the
    # one this page mainly guards against, but it is still overstating.
    if c.needs_file and not (REPO / c.needs_file).exists():
        return UNVERIFIED, c.absent_note or f"{c.needs_file} does not exist yet"
    env = {**os.environ, **c.env}
    try:
        proc = subprocess.run(c.command, cwd=c.cwd, env=env, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return UNVERIFIED, f"timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001 — a broken check is unverified, not passing
        return UNVERIFIED, f"could not run: {exc}"
    if proc.returncode == 0:
        return PASS, "ok"
    return FAIL, _explain(proc)


def _explain(proc) -> str:
    """The most informative line a failing command produced.

    Taking the last line is wrong often enough to matter: a node process ends
    with its own version banner, so the reason a check failed gets replaced by
    "Node.js v22.22.2". A detail nobody can act on makes the page decorative,
    which for this page is the same as dishonest.
    """
    lines = [ln.strip() for ln in
             ((proc.stderr or "") + "\n" + (proc.stdout or "")).splitlines() if ln.strip()]
    if not lines:
        return f"exit {proc.returncode}"
    for marker in ("Error:", "error:", "FAIL", "Refusing", "refus", "mismatch", "does not"):
        for ln in lines:
            if marker in ln:
                return ln[:240]
    return lines[-1][:240]


def render_text(results: list[tuple[Claim, str, str]]) -> str:
    width = max(len(c.claim) for c, _, _ in results)
    out = ["", "$RCLAW — claims and how to check them", "=" * (width + 26), ""]
    for c, state, detail in results:
        out.append(f"  {state:<10}  {c.claim:<{width}}  {detail}")
        out.append(f"  {'':<10}  $ {c.shell}")
        out.append("")
    return "\n".join(out)


def render_markdown(results: list[tuple[Claim, str, str]], *, commit: str) -> str:
    icon = {PASS: "✅", FAIL: "❌", UNVERIFIED: "⚠️"}
    lines = [
        "# $RCLAW — verification receipts",
        "",
        "Every claim below is followed by the command that checks it. Run them yourself;",
        "none of them require trusting this page, a server, or the operator.",
        "",
        f"Generated from commit `{commit}`.",
        "",
        "> **⚠️ UNVERIFIED is not a pass.** It means the check did not run to completion —",
        "> a missing toolchain, no network, or a missing artifact. It is reported separately",
        "> and never rendered as success, because a page that quietly upgrades *unknown* to",
        "> *fine* is worth less than no page.",
        "",
        "| | Claim | Result | Check it yourself |",
        "|---|---|---|---|",
    ]
    for c, state, detail in results:
        lines.append(f"| {icon[state]} | {c.claim} | **{state}** — {detail} | `{c.shell}` |")
    lines += ["", "## Why each one matters", ""]
    for c, state, _ in results:
        lines += [f"### {c.claim}", "", c.why, "", f"```\n{c.shell}\n```", ""]
    counts = {s: sum(1 for _, st, _ in results if st == s) for s in (PASS, FAIL, UNVERIFIED)}
    lines += [
        "## Summary", "",
        f"- **{counts[PASS]}** verified",
        f"- **{counts[FAIL]}** failing",
        f"- **{counts[UNVERIFIED]}** unverified (not checked — see the warning above)",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="skip checks needing a network")
    ap.add_argument("--markdown", action="store_true", help="emit a publishable page")
    ap.add_argument("--out", help="write the markdown to this path instead of stdout")
    ap.add_argument("--allow-unverified", action="store_true",
                    help="exit 0 even when some checks could not run")
    ap.add_argument("--only", help="comma-separated claim keys to run")
    args = ap.parse_args()

    selected = CLAIMS
    if args.only:
        want = {k.strip() for k in args.only.split(",")}
        unknown = want - {c.key for c in CLAIMS}
        if unknown:
            print(f"unknown claim key(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        selected = [c for c in CLAIMS if c.key in want]

    results = []
    for c in selected:
        state, detail = run_claim(c, offline=args.offline)
        if not args.markdown:
            print(f"  {state:<10}  {c.claim}", flush=True)
        results.append((c, state, detail))

    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                                capture_output=True, text=True, timeout=30).stdout.strip() or "unknown"
    except Exception:
        commit = "unknown"

    if args.markdown:
        page = render_markdown(results, commit=commit)
        if args.out:
            Path(args.out).write_text(page, encoding="utf-8")
            print(f"wrote {args.out}")
        else:
            print(page)
    else:
        print(render_text(results))

    failed = [c.key for c, s, _ in results if s == FAIL]
    unver = [c.key for c, s, _ in results if s == UNVERIFIED]
    if failed:
        print(f"FAILING: {', '.join(failed)}", file=sys.stderr)
        return 1
    if unver and not args.allow_unverified:
        print(f"UNVERIFIED (not a pass): {', '.join(unver)}", file=sys.stderr)
        print("Re-run where the toolchain and network are available, or pass "
              "--allow-unverified to accept the gap explicitly.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
