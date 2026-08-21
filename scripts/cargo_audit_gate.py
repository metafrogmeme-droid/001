#!/usr/bin/env python3
"""Supply-chain ratchet for the Rust workspace (RustSec advisories).

Why a ratchet and not plain ``cargo audit``
-------------------------------------------
``Cargo.lock`` currently carries 8 RustSec advisories, every one of them inside
the Solana 1.18 dependency stack that ``rust-toolchain.toml`` and
``Anchor.toml`` pin *deliberately* so the deployed bytecode is reproducible.
Clearing them means moving to a different Solana major, which is a deploy-gating
decision, not something an unrelated pull request should be blocked on. A plain
``cargo audit`` step would therefore be red on its first run and stay red — a
permanently red check is not a control, it is a habit of ignoring checks.

So: the known advisory set is committed to ``.cargo-audit-baseline.json`` and
this fails only on something NEW. Comparison is by advisory id, so swapping one
advisory for a different one of the same severity is still caught.

Shipped vs dev-only
-------------------
Not all of these are equal, and the baseline records which is which. Six of the
eight (``quinn-proto``, ``ring``, ``rustls-webpki``) reach the tree only through
``solana-program-test``/RPC-client dev-dependencies — they are test-harness code
that no deployed artifact contains. Two (``curve25519-dalek``,
``ed25519-dalek``) are in the normal dependency tree via
``solana-sdk``/``anchor-spl`` and so belong to the program's real supply chain.
A new advisory in the normal tree is reported as such, because that is the one
that would actually ship.

Usage::

    python3 scripts/cargo_audit_gate.py            # gate (CI)
    python3 scripts/cargo_audit_gate.py --update   # re-record, deliberately
"""

from __future__ import annotations

import datetime as _dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / ".cargo-audit-baseline.json"
PROGRAM = "rclaw_staking"


def _cargo_audit_bin() -> str:
    """cargo-audit is a separate binary; be explicit about it being missing."""
    for candidate in ("cargo-audit", str(Path.home() / ".cargo" / "bin" / "cargo-audit")):
        if shutil.which(candidate) or Path(candidate).is_file():
            return candidate
    print(
        "cargo-audit is not installed. Install it with:\n"
        "  cargo install cargo-audit --locked",
        file=sys.stderr,
    )
    raise SystemExit(1)


def run_audit() -> dict:
    """Run ``cargo audit --json``.

    The exit code is non-zero whenever anything is found, so it carries no
    information here — the JSON body does. Unparseable output IS fatal: a gate
    that passes when it cannot read its own input is worse than no gate.
    """
    proc = subprocess.run(
        [_cargo_audit_bin(), "audit", "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if not proc.stdout.strip():
        raise SystemExit(
            f"cargo audit produced no output (status {proc.returncode}): "
            f"{proc.stderr[:500]}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"cargo audit did not return JSON (status {proc.returncode}): {exc}"
        ) from exc


def in_shipped_tree(crate: str, version: str) -> bool | None:
    """True / False / None — reachable through NON-dev dependencies, or unknown.

    ``cargo audit`` reads Cargo.lock, which includes dev-dependencies, so this
    is what separates "in the program's supply chain" from "in the test
    harness".

    THE VERSION IS PART OF THE QUESTION, and asking without it was wrong twice
    over. Measured 2026-08-21 against this repo's own tree:

        $ cargo tree -p rclaw_staking -e normal -i borsh
        error: There are multiple `borsh` packages in your project, and the
        specification `borsh` is ambiguous.
          borsh@0.9.3   borsh@0.10.4   borsh@1.5.1

    Exit 101. The old body was ``returncode == 0 and bool(stdout)``, so that
    refusal-to-answer read as False, and False renders as ``[dev-only]`` — the
    reassuring label, on the evidence that nothing could be assessed. All three
    borsh versions ARE in the shipped tree; every chain ends at
    ``rclaw_staking v0.1.0``. The serialization library at the heart of every
    Solana instruction would have been reported as test-harness-only.

    Not a corner case: 24 of the 191 crates in this program's shipped tree are
    present at more than one version — borsh, rand, getrandom, hmac, pbkdf2,
    digest, base64, rand_core among them. That is the crypto and serialization
    surface, which is where RustSec advisories actually land.

    Asking about ``crate@version`` fixes it without parsing cargo's error text:
    the advisory already names the exact version, and a bare crate name was
    always underspecified anyway — two versions of a crate can differ in
    whether they are shipped, and only one of them is the vulnerable one.

    The third value is the point. "cargo could not answer" and "no" are
    different findings, and the old signature could not hold them apart —
    `_book_risk(None)` answering "none", one language over.
    """
    proc = subprocess.run(
        ["cargo", "tree", "-p", PROGRAM, "-e", "normal", "-i", f"{crate}@{version}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        return bool(proc.stdout.strip())
    # cargo's own words for "that package is not in this graph" — a real answer.
    if "did not match any packages" in proc.stderr:
        return False
    return None


def collect(report: dict) -> dict[str, dict]:
    """advisory id -> {crate, version, title, shipped}."""
    found: dict[str, dict] = {}
    # Keyed on (crate, version), not crate: rustls-webpki carries three
    # advisories at one version, so the cache still earns its keep, but two
    # versions of one crate can differ in whether they are shipped and sharing
    # an entry between them would answer for the wrong one.
    shipped_cache: dict[tuple[str, str], bool | None] = {}
    for vuln in report.get("vulnerabilities", {}).get("list", []):
        advisory = vuln.get("advisory", {})
        package = vuln.get("package", {})
        crate = package.get("name", "?")
        version = package.get("version", "?")
        if (crate, version) not in shipped_cache:
            shipped_cache[(crate, version)] = in_shipped_tree(crate, version)
        found[advisory.get("id", "?")] = {
            "crate": crate,
            "version": version,
            "title": (advisory.get("title") or "").strip(),
            "shipped": shipped_cache[(crate, version)],
        }
    return found


def describe(entry: dict) -> str:
    # `is True` / `is False`, not truthiness: None is the third case and it must
    # not fall into the reassuring branch. That is the whole reason the third
    # case exists.
    shipped = entry["shipped"]
    if shipped is True:
        where = "SHIPPED tree"
    elif shipped is False:
        where = "dev-only"
    else:
        where = "UNKNOWN tree — cargo could not say"
    return f"  [{where}] {entry['crate']} {entry['version']} — {entry['title']}"


def main() -> int:
    report = run_audit()
    found = collect(report)

    if "--update" in sys.argv:
        BASELINE.write_text(
            json.dumps(
                {
                    "_comment": (
                        "Known RustSec advisories in Cargo.lock, recorded deliberately. "
                        "This is a RATCHET floor, not an approval: every entry is an "
                        "outstanding advisory. 'shipped' means the crate is reachable "
                        "through non-dev dependencies of the program and therefore "
                        "belongs to its real supply chain; the rest are test-harness "
                        "only. Regenerate with `python3 scripts/cargo_audit_gate.py "
                        "--update` only after reviewing the delta."
                    ),
                    "recorded": _dt.date.today().isoformat(),
                    "advisories": dict(sorted(found.items())),
                },
                indent=2,
            )
            + "\n"
        )
        print(f"Baseline updated: {len(found)} advisories")
        return 0

    if not BASELINE.exists():
        print(
            f"No {BASELINE.name}. Create it with: "
            "python3 scripts/cargo_audit_gate.py --update",
            file=sys.stderr,
        )
        return 1

    baseline = json.loads(BASELINE.read_text())
    known = baseline.get("advisories", {})

    new_ids = sorted(set(found) - set(known))
    gone_ids = sorted(set(known) - set(found))
    # Counted, not subtracted. `dev_only = len(found) - shipped_now` is the
    # `losses = len(all) - wins` shape CLAUDE.md names outright: it files every
    # unscorable row under the reassuring heading. Each bucket is counted from
    # its own predicate, and they are asserted to add up.
    shipped_now = sum(1 for e in found.values() if e["shipped"] is True)
    dev_only_now = sum(1 for e in found.values() if e["shipped"] is False)
    unknown_now = sum(1 for e in found.values() if e["shipped"] is None)
    assert shipped_now + dev_only_now + unknown_now == len(found)

    summary = f"{shipped_now} in the shipped tree, {dev_only_now} dev-only"
    if unknown_now:
        summary += f", {unknown_now} UNKNOWN (cargo could not resolve)"
    print(f"RustSec advisories in Cargo.lock: {len(found)} ({summary})")
    print(f"baseline ({baseline.get('recorded', '?')}): {len(known)}")

    if gone_ids:
        print(
            f"\n{len(gone_ids)} baselined advisor"
            f"{'y is' if len(gone_ids) == 1 else 'ies are'} gone "
            "— tighten the floor with `--update`: " + ", ".join(gone_ids)
        )

    if not new_ids:
        print("\nNo new advisories. (The baselined backlog above is still outstanding.)")
        return 0

    print("\n=== NEW RUSTSEC ADVISORIES ===", file=sys.stderr)
    for advisory_id in new_ids:
        print(f"{advisory_id}", file=sys.stderr)
        print(describe(found[advisory_id]), file=sys.stderr)
    # `is not False`, so UNKNOWN escalates rather than staying quiet. This line
    # is the one that tells an operator a new advisory touches the deployed
    # program; withholding it because we could not classify the crate is the
    # calm-verdict-on-no-evidence failure this whole function just came off.
    if any(found[i]["shipped"] is not False for i in new_ids):
        unknown_new = [i for i in new_ids if found[i]["shipped"] is None]
        print(
            "\nAt least one is in the SHIPPED dependency tree — it would be part of the "
            "deployed program's supply chain, not just the test harness.",
            file=sys.stderr,
        )
        if unknown_new:
            print(
                f"({len(unknown_new)} of them could not be classified at all; "
                "treat as shipped until you have checked by hand with "
                f"`cargo tree -p {PROGRAM} -e normal -i <crate>@<version>`.)",
                file=sys.stderr,
            )
    print(
        "\nUpgrade the affected crate, or if the advisory genuinely does not apply, say "
        "why in the commit message and re-record with `--update`.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
