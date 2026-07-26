#!/usr/bin/env python3
"""Prove the deployed bytecode is reproducible and that the mint pin is in it.

Why this exists
---------------
``programs/rclaw_staking/src/lib.rs`` declares::

    pub const PINNED_MINT: Option<&str> = option_env!("RCLAW_PINNED_MINT");

That is a **build-time environment variable which changes the emitted
bytecode**, and it gates ``enforce_pinned_mint`` — the check that stops a user
staking a worthless token to buy a tier. An identical commit therefore produces
two different deployable artifacts depending on an input recorded nowhere in
the repository, in CI, or on chain.

The consequence is not that the wrong one might ship. It is that **nobody can
tell which one did**. A verifier who clones the tagged commit, rebuilds, and
gets a hash that differs from the deployed program cannot distinguish "the
deployer legitimately set the pin" from "the deployer stripped the mint check
before deploying" — the two look identical from outside. The check that was
supposed to catch a malicious upgrade yields no signal in either direction.

This was not theoretical. The first SBF build of this program was made and
deployed to a local validator with ``RCLAW_PINNED_MINT`` unset, and the smoke
test then successfully staked a **randomly generated** mint — which is exactly
what an unpinned build is supposed to allow, and exactly what nothing recorded.

What this gate actually checks
------------------------------
Two properties, both established by measurement before being written down:

1. **The build is reproducible.** Building the same source twice yields
   byte-identical bytecode. Verified across a full clean rebuild at a different
   absolute path (``/tmp/reprocheck`` vs the repo root) — the output was
   byte-identical, so the build does not embed its own path, a timestamp, or a
   random seed. This is the property that makes a published hash mean anything
   at all; without it, comparing hashes is theatre and this gate would be
   telling you a lie in a reassuring tone.

2. **The pin is load-bearing.** A pinned build and an unpinned build produce
   *different* hashes. If they ever match, ``RCLAW_PINNED_MINT`` is no longer
   reaching the binary — the ``option_env!`` was refactored away, the constant
   was optimised out, or the variable is being read under a different name —
   and every deployment is silently unpinned regardless of what the operator
   typed on the command line. That failure is invisible from the source, which
   still reads as though the pin works.

Together they make one publishable claim checkable by a stranger: *"the release
artifact is sha256 X, built from commit Y with RCLAW_PINNED_MINT=Z."* Rebuild
with Z, compare to X. A deployer who lied about Z cannot make the hashes agree.

What this gate does NOT check
-----------------------------
It does not verify that any *deployed* program matches any *source*. That
requires reading the on-chain programdata and is a deploy-time step
(``solana-verify verify-from-repo``), not a per-commit one. This gate
establishes the precondition that makes that step meaningful.

Reproducibility is asserted **within one toolchain**. Solana's platform-tools
version is pinned in ``Anchor.toml``, and a different major would be expected to
produce different bytecode from identical source. That is why the toolchain
version is recorded alongside the hash rather than assumed.

Usage::

    python3 scripts/build_provenance_gate.py            # run the checks
    python3 scripts/build_provenance_gate.py --record   # also emit a provenance record

Exit codes: 0 pass, 1 a property failed, 2 could not run the build at all.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "programs" / "rclaw_staking" / "Cargo.toml"
ARTIFACT = REPO / "target" / "deploy" / "rclaw_staking.so"
SOURCE = REPO / "programs" / "rclaw_staking" / "src" / "lib.rs"

# A syntactically valid mint that is deliberately NOT any real $RCLAW mint. This
# is only ever used to observe that setting the variable changes the output; it
# must never be the value a release is built with.
SENTINEL_MINT = "So11111111111111111111111111111111111111112"


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)


def sbf_available() -> bool:
    return shutil.which("cargo-build-sbf") is not None


def build(pinned: str | None) -> str:
    """Build the program and return the sha256 of the bytecode.

    ``lib.rs`` is touched first as insurance against a cache hit serving the
    previous build's artifact, which would have this script compare a hash
    against itself and pass unconditionally.

    On the pinned toolchain (cargo-build-sbf 1.18.26) that insurance turns out
    to be redundant: cargo records ``option_env!`` inputs in its fingerprint, so
    changing ``RCLAW_PINNED_MINT`` alone already forces a rebuild. Measured, not
    assumed — removing the touch was tried and the gate still produced two
    distinct hashes. It is kept because the cost is a 4-second recompile and the
    failure mode it guards against is a silent false pass, but it is *not* the
    thing making this gate work, and saying otherwise would overstate it.
    """
    SOURCE.touch()
    env = dict(os.environ)
    if pinned is None:
        env.pop("RCLAW_PINNED_MINT", None)
    else:
        env["RCLAW_PINNED_MINT"] = pinned
    proc = subprocess.run(
        ["cargo-build-sbf", "--manifest-path", str(MANIFEST)],
        env=env, cwd=REPO, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        _fail("cargo-build-sbf failed:\n" + (proc.stderr or proc.stdout)[-2000:])
        sys.exit(2)
    if not ARTIFACT.exists():
        _fail(f"build reported success but {ARTIFACT} does not exist")
        sys.exit(2)
    return hashlib.sha256(ARTIFACT.read_bytes()).hexdigest()


def toolchain() -> dict:
    def ver(*cmd: str) -> str:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            return (out.stdout or out.stderr).strip().splitlines()[0]
        except Exception:
            return "unknown"
    return {
        "cargo-build-sbf": ver("cargo-build-sbf", "--version"),
        "rustc": ver("rustc", "--version"),
        "solana": ver("solana", "--version"),
    }


def head_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, timeout=30
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--record", action="store_true",
                    help="write a provenance record to stdout as JSON")
    args = ap.parse_args()

    if not sbf_available():
        # Not a pass. A gate that goes green when it could not run is worse than
        # no gate: it converts "unverified" into "verified" in the reader's head.
        _fail("cargo-build-sbf is not on PATH — cannot verify the deployable "
              "artifact. Install the pinned Solana toolchain (see the CI job "
              "'Install the SBF toolchain') or skip this gate explicitly.")
        return 2

    started = time.time()
    print("Building three times (unpinned, pinned, pinned again)...")
    unpinned = build(None)
    print(f"  unpinned        : {unpinned}")
    pinned_a = build(SENTINEL_MINT)
    print(f"  pinned          : {pinned_a}")
    pinned_b = build(SENTINEL_MINT)
    print(f"  pinned (repeat) : {pinned_b}")
    print(f"  ({time.time() - started:.0f}s)\n")

    ok = True

    # Property 1 — reproducible.
    if pinned_a != pinned_b:
        _fail(
            "the same source built twice produced different bytecode.\n"
            "  A published artifact hash is now unverifiable: a rebuild will not\n"
            "  match no matter who does it, so a substituted binary and an honest\n"
            "  one are indistinguishable. Something non-deterministic entered the\n"
            "  build — a timestamp, a random seed, an embedded absolute path, or\n"
            "  parallel codegen without a fixed unit count."
        )
        ok = False
    else:
        print("PASS  reproducible — identical source, identical bytecode")

    # Property 2 — the pin reaches the binary.
    if unpinned == pinned_a:
        _fail(
            "setting RCLAW_PINNED_MINT did not change the bytecode.\n"
            "  PINNED_MINT is therefore NOT compiled in, and enforce_pinned_mint\n"
            "  accepts any mint in every build — including release builds where\n"
            "  the operator set the variable and believes it took effect. The\n"
            "  source at programs/rclaw_staking/src/lib.rs still reads as though\n"
            "  the pin works, which is why this is checked by measurement."
        )
        ok = False
    else:
        print("PASS  the mint pin is load-bearing — it changes the deployed bytecode")

    if args.record:
        record = {
            "_what": "Build inputs for the rclaw_staking deployable artifact. To "
                     "verify a deployment: rebuild this commit with the recorded "
                     "RCLAW_PINNED_MINT and compare sha256 against the deployed "
                     "programdata.",
            "commit": head_commit(),
            "toolchain": toolchain(),
            "artifacts": {
                "RCLAW_PINNED_MINT unset": unpinned,
                f"RCLAW_PINNED_MINT={SENTINEL_MINT} (sentinel, not a release value)": pinned_a,
            },
        }
        print("\n" + json.dumps(record, indent=2))

    if not ok:
        return 1
    print("\nBuild provenance gate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
