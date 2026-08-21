"""`[dev-only]` printed for a crate cargo refused to classify.

`in_shipped_tree` was `proc.returncode == 0 and bool(proc.stdout.strip())`, so
every way of failing collapsed into False, and False renders as `dev-only` —
"test harness only, not in the deployed program", the reassuring half of a
security label.

Measured against this repo's own tree on 2026-08-21:

    $ cargo tree -p rclaw_staking -e normal -i borsh
    error: There are multiple `borsh` packages in your project, and the
    specification `borsh` is ambiguous.
      borsh@0.9.3   borsh@0.10.4   borsh@1.5.1
    (exit 101)

All three ARE in the shipped tree — every chain ends at `rclaw_staking
v0.1.0`. The serialization library at the heart of every Solana instruction
classified as test-harness-only, because the question was asked in a form
cargo cannot answer.

Not a corner case: 24 of the 191 crates in the shipped tree are present at
more than one version — borsh, rand, getrandom, hmac, pbkdf2, digest, base64,
rand_core among them. That is the crypto and serialization surface, which is
where RustSec advisories land.

No cargo here. The fault is entirely in how the subprocess result is READ, so
the subprocess is faked and the real bytes cargo emitted are replayed into it.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parent.parent / "scripts" / "cargo_audit_gate.py"

# Replayed verbatim from the command above.
AMBIGUOUS_STDERR = (
    "error: There are multiple `borsh` packages in your project, and the "
    "specification `borsh` is ambiguous.\n"
    "Please re-run this command with one of the following specifications:\n"
    "  borsh@0.9.3\n  borsh@0.10.4\n  borsh@1.5.1\n"
)
NO_MATCH_STDERR = (
    "error: package ID specification `definitely-not-a-crate-xyz` did not "
    "match any packages\n"
)


@pytest.fixture
def gate():
    spec = importlib.util.spec_from_file_location("cargo_audit_gate", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fake_cargo(monkeypatch, gate, *, returncode, stdout="", stderr=""):
    """Replace subprocess.run and record the argv it was handed."""
    seen: list[list[str]] = []

    def _run(cmd, **kwargs):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    monkeypatch.setattr(gate.subprocess, "run", _run)
    return seen


class TestCargoRefusingToAnswerIsNotAnAnswer:
    def test_an_ambiguous_spec_is_unknown_not_dev_only(self, gate, monkeypatch):
        fake_cargo(monkeypatch, gate, returncode=101, stderr=AMBIGUOUS_STDERR)
        assert gate.in_shipped_tree("borsh", "0.10.4") is None, (
            "cargo exited 101 saying it could not tell; anything other than "
            "None here is a verdict manufactured from a refusal"
        )

    def test_it_is_specifically_not_false(self, gate, monkeypatch):
        """The bug, stated as its own test.

        `is None` above would also pass if the function returned None for
        everything. This is the assertion that fails against the old body,
        which returned exactly False here.
        """
        fake_cargo(monkeypatch, gate, returncode=101, stderr=AMBIGUOUS_STDERR)
        assert gate.in_shipped_tree("borsh", "0.10.4") is not False

    def test_cargos_own_words_for_no_are_still_a_no(self, gate, monkeypatch):
        """Unknown must not swallow the genuine negative — that would make
        every dev-only advisory escalate, and a gate that always escalates
        stops carrying information."""
        fake_cargo(monkeypatch, gate, returncode=101, stderr=NO_MATCH_STDERR)
        assert gate.in_shipped_tree("nope", "1.0.0") is False

    def test_a_clean_resolve_with_output_is_shipped(self, gate, monkeypatch):
        fake_cargo(monkeypatch, gate, returncode=0,
                   stdout="borsh v0.10.4\n└── rclaw_staking v0.1.0\n")
        assert gate.in_shipped_tree("borsh", "0.10.4") is True

    def test_a_clean_resolve_with_no_output_is_not_shipped(self, gate, monkeypatch):
        """cargo exits 0 and prints `warning: nothing to print.` to STDERR for
        a package that resolves but is not in the non-dev graph. That is how
        ring@0.16.20 is correctly classified, and stdout is what decides."""
        fake_cargo(monkeypatch, gate, returncode=0, stdout="",
                   stderr="warning: nothing to print.\n")
        assert gate.in_shipped_tree("ring", "0.16.20") is False


class TestTheLabelTheOperatorReads:
    """`describe()` is where the third value either survives or is thrown away.

    Truthiness would have collapsed None back into the `dev-only` branch and
    undone the whole fix silently, which is why it tests `is True` / `is False`.
    """

    def _entry(self, shipped):
        return {"crate": "borsh", "version": "0.10.4",
                "title": "Some advisory", "shipped": shipped}

    def test_unknown_does_not_render_as_dev_only(self, gate):
        out = gate.describe(self._entry(None))
        assert "dev-only" not in out, out
        assert "UNKNOWN" in out, out

    def test_unknown_says_who_could_not_say(self, gate):
        assert "cargo could not say" in gate.describe(self._entry(None))

    def test_the_two_known_verdicts_are_unchanged(self, gate):
        assert "[SHIPPED tree]" in gate.describe(self._entry(True))
        assert "[dev-only]" in gate.describe(self._entry(False))


class TestUnknownsAreNotCountedAsDevOnly:
    """The summary line subtracted: `len(found) - shipped_now`.

    That is `losses = len(all) - wins` — every unscorable row filed under the
    reassuring heading. Each bucket is counted from its own predicate now, and
    the three are asserted to add up inside the gate itself.
    """

    def test_the_three_buckets_are_counted_independently(self, gate):
        found = {
            "A": {"shipped": True}, "B": {"shipped": False}, "C": {"shipped": None},
        }
        shipped = sum(1 for e in found.values() if e["shipped"] is True)
        dev_only = sum(1 for e in found.values() if e["shipped"] is False)
        unknown = sum(1 for e in found.values() if e["shipped"] is None)
        assert (shipped, dev_only, unknown) == (1, 1, 1)
        assert shipped + dev_only + unknown == len(found)
        # The shape being replaced, shown failing on the same data.
        assert len(found) - shipped == 2, "subtraction cannot separate the two"

    def test_an_unknown_new_advisory_still_escalates(self, gate):
        """`any(shipped)` would stay silent on an unclassifiable advisory —
        withholding the one line that says a new advisory touches the deployed
        program, precisely when nobody knows whether it does."""
        found = {"NEW": {"shipped": None}}
        assert not any(found[i]["shipped"] for i in found), "the old predicate"
        assert any(found[i]["shipped"] is not False for i in found), "the new one"


class TestTheVersionIsPartOfTheQuestion:
    """The mechanism, not just the outcome — asking `-i borsh` is what created
    the ambiguity in the first place, so pin that it is never asked that way."""

    def test_the_query_names_the_exact_version(self, gate, monkeypatch):
        seen = fake_cargo(monkeypatch, gate, returncode=0, stdout="x\n")
        gate.in_shipped_tree("borsh", "0.10.4")
        assert seen, "cargo was never invoked"
        argv = seen[0]
        # The argument to -i is the whole question. Read it directly rather
        # than testing for the ABSENCE of "borsh" somewhere in the argv — the
        # first draft of this test did that and sliced itself down to an empty
        # list, so it passed against any argv at all. Asserting a short string
        # is absent is the assertion that keeps misfiring in this repo.
        spec = argv[argv.index("-i") + 1]
        assert spec == "borsh@0.10.4", f"asked cargo for {spec!r}"
        assert spec != "borsh", "a bare crate name is the form cargo calls ambiguous"
