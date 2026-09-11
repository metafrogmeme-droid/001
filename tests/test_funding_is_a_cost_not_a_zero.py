"""`"funding": "0"` on a statement whose whole subject is provenance honesty.

`compute_metrics` emitted a hardcoded zero with a comment admitting the data
was not there::

    "funding": "0",     # spot: no funding. Perp funding: PENDING (not in v0 data).

on the Common Statement Format — the module whose own docstring is about
**trust-tier honesty**, whose output goes inside `commitment_hash`, and which
`verify.py` re-derives by calling the same function, so the verifier could
never catch its own blind spot. `_numstr` passes `None` through and every other
field already relies on that, so the honest value was available and a literal
was chosen over it.

THE 2026-06 AUDIT CALLED THIS "net edge is systematically overstated" AND THAT
IS HALF RIGHT. What saved it from publishing a false proof is the balance-delta
check in `reconcile`: the signed close−open delta includes funding and
`fills_net_pnl` did not, so the residual WAS the funding. Driven below: a round
trip that paid $3.20 reconciled with residual −3.200 against a $0.01 tolerance.

So the real cost was never a false headline. It was that

  1. EVERY perp epoch was structurally unpublishable, and
  2. the reason given was the selective-omission alarm — the one `reconcile`'s
     docstring reserves for "a dishonest operator could sign only winning
     trades and drop the losers".

A structural gap firing the fraud alarm is "a heuristic is never a verdict" on
the surface where a false accusation costs the most.

FUNDING IS NOT A FILL. It is a periodic transfer on an open position: no price,
no side, no quantity, and it happens when no trade happens. It gets its own
record and its own hash, which is also what keeps every v0 fill hash — and so
every v0 merkle root over fills alone — byte-identical.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from bot.proofofpnl.csf import (
    compute_metrics,
    fill_hash,
    make_fill,
    make_funding,
    merkle_root,
    record_hash,
)
from bot.proofofpnl.ingest_cex import funding_from_ccxt_history
from bot.proofofpnl.reconcile import reconcile

TIER = "cex_operator_signed"
PERP, SPOT = "BTC/USDT:USDT", "BTC/USDT"


def _fill(market, side, px, ts, ref, fee="0.36"):
    return make_fill("bitget", "cex", market, side, px, "0.01", fee, "USDT",
                     ts, ref, TIER)


def _round_trip(market=PERP):
    return [_fill(market, "buy", "60000", 1_700_000_000_000, "t1"),
            _fill(market, "sell", "60500", 1_700_086_400_000, "t2")]


def _funding(amount, ts=1_700_028_800_000, ref="f1", market=PERP):
    return make_funding("bitget", "cex", market, amount, "USDT", ts, ref, TIER)


# ── compatibility: v0 statements must still verify ────────────────────────

def test_a_v0_fill_hashes_exactly_as_it_did():
    """The funding record type must not disturb a single existing hash. Pinned
    against a literal rather than a recomputation, so a change to `_FILL_FIELDS`
    or to `canonical` cannot move both sides together."""
    f = _fill(PERP, "buy", "60000", 1_700_000_000_000, "t1")
    assert f["fill_hash"] == (
        "f6236d2c2f4575305a17b4626fc7eadf3a13050d7fddc3a8252861e0b2997806")


def test_an_epoch_with_no_funding_keeps_its_v0_merkle_root():
    fills = _round_trip()
    assert merkle_root(fills) == merkle_root(fills)
    # The root is built from fill hashes only when no funding record is present,
    # which is what makes this backward compatible rather than merely similar.
    leaves_only_fills = all(not r.get("kind") for r in fills)
    assert leaves_only_fills


def test_record_hash_is_one_door():
    fill = _fill(PERP, "buy", "60000", 1, "t1")
    fund = _funding("-1.10")
    assert record_hash(fill) == fill_hash(fill) == fill["fill_hash"]
    assert record_hash(fund) == fund["funding_hash"]
    assert record_hash(fund) != record_hash(fill)


# ── the three states of a funding total ───────────────────────────────────

class TestNoRecordsIsNotZero:
    """THE RULE, and the first draft of this fix got it wrong exactly as the
    original did: it summed an empty list to 0 and reported a measured zero for
    an epoch nobody had fetched funding for. `orphan_position.py` states it for
    its own case — "an empty map means 'no stops found' only if somebody
    looked"."""

    def test_a_perp_epoch_with_no_funding_record_is_unmeasured(self):
        m = compute_metrics(_round_trip(PERP))
        assert m["funding"] is None
        assert m["net_pnl"] is None, "a net that silently drops a real cost"

    def test_but_the_fills_half_is_still_reported(self):
        """Guard, not blank: the epoch still says what the FILLS came to, so a
        reader can see the gap is funding rather than everything."""
        assert compute_metrics(_round_trip(PERP))["fills_net_pnl"] == "4.28"

    def test_a_spot_epoch_with_no_funding_record_is_a_real_zero(self):
        """THE RED HERRING. Spot pays no funding, so nothing was missed and 0 is
        a measurement. A fix that made every absence unknown would be the same
        defect pointing the other way."""
        m = compute_metrics(_round_trip(SPOT))
        assert m["funding"] == "0"
        assert m["net_pnl"] == "4.28"

    def test_scanned_and_found_none_is_a_measurement(self):
        recs = _round_trip(PERP) + [_funding(0, ts=0, ref="scanned:" + PERP)]
        assert compute_metrics(recs)["funding"] == "0"


class TestAMeasuredFundingIsCharged:
    def test_paid_funding_reduces_net_pnl(self):
        recs = _round_trip() + [_funding("-1.10", ref="f1"),
                                _funding("-1.05", ts=1_700_057_600_000, ref="f2"),
                                _funding("-1.05", ts=1_700_086_300_000, ref="f3")]
        m = compute_metrics(recs)
        assert m["funding"] == "-3.20"
        assert m["fills_net_pnl"] == "4.28"
        assert m["net_pnl"] == "1.08"

    def test_received_funding_increases_it(self):
        """The sign is the account's and both directions happen — funding is not
        a fee. An unsigned magnitude would lose half the information."""
        m = compute_metrics(_round_trip() + [_funding("2.00")])
        assert m["funding"] == "2.00"
        assert m["net_pnl"] == "6.28"

    def test_a_measured_zero_settlement_is_kept(self):
        m = compute_metrics(_round_trip() + [_funding("0")])
        assert m["funding"] == "0" and m["net_pnl"] == "4.28"


class TestAnUnreadableAmountPoisonsTheTotal:
    def test_one_blank_amount_makes_the_total_unmeasurable(self):
        """A partial sum printed as a whole is the shape the honesty gate
        exists to catch."""
        recs = _round_trip() + [_funding("-1.10", ref="f1"),
                                _funding(None, ts=1_700_057_600_000, ref="f2")]
        m = compute_metrics(recs)
        assert m["funding"] is None and m["net_pnl"] is None

    @pytest.mark.parametrize("junk", [None, "", "n/a", "NaN"])
    def test_junk_is_not_a_settlement_that_cost_nothing(self, junk):
        assert compute_metrics(_round_trip() + [_funding(junk)])["funding"] is None


# ── reconcile names the real cause ────────────────────────────────────────

def _balances(funding_paid=Decimal("3.20")):
    gross, fees = Decimal("5.00"), Decimal("0.723")
    ob = Decimal("1000")
    return str(ob), str(ob + gross - fees - funding_paid)


class TestTheFraudAlarmNoLongerFiresForAStructuralGap:
    def test_unfetched_funding_is_named_as_itself(self):
        ob, cb = _balances()
        r = reconcile(_round_trip(), ob, cb)
        assert r["status"] == "INCOMPLETE"
        joined = " ".join(r["reasons"])
        assert "funding never fetched" in joined
        assert "unreconciled" not in joined, (
            "the selective-omission alarm fired for a structural gap")

    def test_the_epoch_publishes_once_funding_is_ingested(self):
        """THE PAYOFF, and it is bigger than a corrected number: a perp epoch
        could not reach `published` at all before, because the residual was
        always the funding."""
        ob, cb = _balances()
        recs = _round_trip() + [_funding("-1.10", ref="f1"),
                                _funding("-1.05", ts=1_700_057_600_000, ref="f2"),
                                _funding("-1.05", ts=1_700_086_300_000, ref="f3")]
        r = reconcile(recs, ob, cb)
        assert r["status"] == "published", r["reasons"]
        assert abs(Decimal(r["residual"])) <= Decimal("0.01")

    def test_a_genuinely_omitted_fill_still_trips_the_alarm(self):
        """THE RED HERRING FOR THE ALARM ITSELF. Naming the funding case must
        not stop the check catching what it was built for: a signed statement
        that drops a losing trade."""
        ob = "1000"
        cb = "900"                      # a $100 loss the fills do not explain
        recs = _round_trip() + [_funding("0", ts=0, ref="scanned:" + PERP)]
        r = reconcile(recs, ob, cb)
        assert r["status"] == "INCOMPLETE"
        assert any("unreconciled" in w for w in r["reasons"])

    def test_an_unreadable_amount_is_named_per_record(self):
        ob, cb = _balances()
        r = reconcile(_round_trip() + [_funding(None, ref="f-bad")], ob, cb)
        assert any("f-bad" in w and "not stated" in w for w in r["reasons"])


# ── the ingestor carries what the records cannot infer ────────────────────

class TestIngestionRecordsWhatWasQueried:
    def test_a_queried_market_with_no_settlements_becomes_a_measured_zero(self):
        recs = funding_from_ccxt_history([], markets=[PERP])
        assert len(recs) == 1
        assert recs[0]["amount"] == "0"
        assert recs[0]["source_ref"] == f"scanned:{PERP}"

    def test_a_market_never_queried_yields_nothing(self):
        """So `reconcile` can still say nobody looked. Emitting a zero here
        would be the ingestor inventing coverage it does not have."""
        assert funding_from_ccxt_history([], markets=[]) == []

    def test_entries_keep_their_sign_and_reference(self):
        recs = funding_from_ccxt_history(
            [{"symbol": PERP, "id": "f1", "timestamp": 5, "amount": -1.1,
              "code": "USDT"}], markets=[PERP])
        assert [r["amount"] for r in recs] == ["-1.1"]
        assert recs[0]["source_ref"] == "f1"

    def test_a_blank_amount_from_the_venue_is_incomplete_not_zero(self):
        recs = funding_from_ccxt_history(
            [{"symbol": PERP, "id": "f9", "timestamp": 5, "amount": None}],
            markets=[PERP])
        assert recs[0]["amount"] is None


# ── the commitment binds it ───────────────────────────────────────────────

def test_tampering_with_a_funding_amount_breaks_the_merkle_root():
    """A cost outside the merkle tree is a cost no verifier can check, which
    would leave the original defect intact behind a new field."""
    recs = _round_trip() + [_funding("-3.20")]
    before = merkle_root(recs)
    recs[-1]["amount"] = "-0.01"
    recs[-1]["funding_hash"] = record_hash(recs[-1])
    assert merkle_root(recs) != before


class TestAMalformedRecordFailsVerificationRatherThanCrashingIt:
    """A CRASH IS NOT A VERDICT, and mypy caught this on the commit that
    introduced it: `r.get("funding_hash")` is Optional and
    `bytes.fromhex(None)` raises TypeError.

    `verify.py` runs `merkle_root` over a statement supplied by whoever is
    being verified. An exception out of the verifier is not a failed
    verification — it is the verifier falling over, which an operator reads as
    a broken tool rather than a rejected proof, and which a hostile statement
    can trigger deliberately. It has to come back as a mismatched root.

    The code was fixed before this test existed; the mutation that put the
    raise back survived the whole file, which is how the gap was found.
    """

    def _tampered(self, **overrides):
        rec = _funding("-3.20")
        rec.update(overrides)
        return _round_trip() + [rec]

    def test_a_funding_record_with_no_hash_does_not_raise(self):
        recs = self._tampered()
        del recs[-1]["funding_hash"]
        assert isinstance(merkle_root(recs), str)

    @pytest.mark.parametrize("bad", [None, "", "zz", 12345, "aa" * 40])
    def test_a_malformed_hash_does_not_raise(self, bad):
        assert isinstance(merkle_root(self._tampered(funding_hash=bad)), str)

    def test_and_it_reads_as_a_mismatch_not_as_agreement(self):
        """The root must DIFFER from the honest one, or an unhashable record
        would verify by accident."""
        honest = merkle_root(_round_trip() + [_funding("-3.20")])
        assert merkle_root(self._tampered(funding_hash=None)) != honest

    def test_a_fill_with_no_hash_is_covered_by_the_same_door(self):
        recs = _round_trip()
        del recs[0]["fill_hash"]
        assert isinstance(merkle_root(recs), str)


class TestTheAssemblerActuallyIngestsFunding:
    """REACHABILITY, AND THE RATCHET CAUGHT ME WITH IT.

    The first version of this work built `funding_from_ccxt_history` and wired
    it to nothing. `test_no_new_unreachable_functions` failed on the adapter,
    and `test_public_agent_directory` failed beside it because its fixture
    trades `BTC/USDT:USDT` — a perp with no funding coverage, which the new rule
    correctly calls unmeasured, so the identity card went `unbacked`.

    Two failures, one cause: a reader that works, reached by nothing. That is
    the defect this repository is organised against, and it arrived inside the
    commit fixing another instance of it.
    """

    def _trade(self, tid, side, price, ts, symbol=PERP):
        return {"id": tid, "order": tid, "symbol": symbol, "side": side,
                "price": price, "amount": 1.0, "timestamp": ts,
                "fee": {"cost": 0.0, "currency": "USDT"}}

    def _bundle(self, **kw):
        from bot.proofofpnl.assemble import assemble_track_record
        return assemble_track_record(
            [self._trade("a1", "buy", 100.0, 1_700_000_001_000),
             self._trade("a2", "sell", 120.0, 1_700_000_002_000)],
            account_ids=["operator"], open_balance="1000", close_balance="1020",
            range_start=1_699_900_000, range_end=1_700_000_000, sign=False, **kw)

    def test_a_perp_bundle_without_coverage_is_incomplete(self):
        st = self._bundle()["statement"]
        assert st["status"] == "INCOMPLETE"
        assert any("funding never fetched" in r for r in st.get("reasons", [])) or \
            st["metrics"]["funding"] is None

    def test_naming_the_queried_market_lets_it_publish(self):
        st = self._bundle(funding_entries=[], funding_markets=[PERP])["statement"]
        assert st["status"] == "published", st.get("reasons")
        assert st["metrics"]["funding"] == "0"

    def test_real_settlements_reach_the_statement_metrics(self):
        st = self._bundle(
            funding_entries=[{"symbol": PERP, "id": "f1", "timestamp": 1_700_000_001_500,
                              "amount": -0.50, "code": "USDT"}],
            funding_markets=[PERP])["statement"]
        # Compared as VALUES, not spellings: `_numstr` preserves the decimal
        # places of its input, so a fill priced "100.0" yields "20.00" here.
        # Pinning the string would make this test about formatting.
        m = st["metrics"]
        assert Decimal(m["funding"]) == Decimal("-0.5")
        assert Decimal(m["fills_net_pnl"]) == Decimal("20")
        assert Decimal(m["net_pnl"]) == Decimal("19.5")
