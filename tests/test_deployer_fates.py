"""What became of a deployer's previous contracts — and the four ways of lying.

`deployer_history` read `prior_rugged` from the day it was written and nothing
could supply it: an explorer says who deployed what, never what became of it.
This is the pass that fills the column, and almost every test here is about a
conclusion it must REFUSE to draw, because every one of them points the same
damaging way.

The load-bearing distinction: a price feed can prove a market EXISTS and can
prove a market IS GONE. It cannot prove a rug — "the liquidity left" and
"somebody pulled the liquidity" are the same reading, and the difference is
intent. So this module writes `prior_dead`, never `prior_rugged`, and
`deployer_history` scores dead as a soft ratio with no hard threshold.

    A MARKET THAT ENDED IS NOT A MARKET THAT WAS STOLEN.
"""
from __future__ import annotations

import asyncio

import pytest

from bot.core import deployer_fates as df
from bot.core.deployer_history import assess_deployer

NOW = 1_700_000_000.0
OLD = (NOW - 200 * 86400) * 1000.0     # pair created 200 days ago, in ms
YOUNG = (NOW - 3 * 86400) * 1000.0     # 3 days ago


def feats(liq=None, vol=None, created=None):
    out = {}
    if liq is not None:
        out["liquidity_usd"] = liq
    if vol is not None:
        out["volume_24h_usd"] = vol
    if created is not None:
        out["pair_created_at_ms"] = created
    return out


def fate(features):
    return df.classify(features, NOW)[0]


# ── the two facts it may assert ───────────────────────────────────────────

def test_a_deep_trading_pool_is_alive():
    assert fate(feats(liq=50_000, vol=1_200, created=OLD)) == df.ALIVE


def test_a_drained_old_pool_is_dead():
    assert fate(feats(liq=12, vol=0, created=OLD)) == df.DEAD


# ── the conclusions it must refuse ────────────────────────────────────────

def test_an_unindexed_contract_is_not_dead():
    """A deployer's history is full of contracts that were never tokens.

    Proxies, multisigs, NFT collections, registries, factories — none has a DEX
    pair and none of them died. Calling them dead would manufacture a record of
    failure out of a contract that was never a market.
    """
    assert fate({}) == df.UNRESOLVED
    assert fate(None) == df.UNRESOLVED


def test_a_failed_lookup_is_not_a_bad_fate():
    """`dead` is the damaging direction, so every read failure lands away from it."""
    class Boom:
        async def fetch(self, chain, address):
            raise RuntimeError("dexscreener down")

    out = asyncio.run(df.resolve_fates(['0xa'], source=Boom(), now=lambda: NOW))
    assert out["prior_dead"] == 0, "an outage was scored as a dead market"
    assert out["prior_alive"] == 0
    assert out["unresolved"] == 1
    assert out["fates"][0]["reason"] == "lookup failed"


def test_a_young_empty_pool_has_not_ended_it_has_not_started():
    # Liquidity near zero on a three-day-old pair is a token that has not
    # launched, not one that died.
    assert fate(feats(liq=50, vol=0, created=YOUNG)) == df.UNRESOLVED


def test_a_drained_pool_of_unknown_age_is_not_dated_and_so_not_dead():
    # Without a creation date the age guard cannot run, and a fate that cannot
    # be dated is not asserted.
    assert fate(feats(liq=50, vol=0)) == df.UNRESOLVED


def test_the_middle_band_is_neither():
    # Between the two thresholds neither statement is true. The gap is not
    # indecision — it is the honest answer.
    assert fate(feats(liq=5_000, vol=10, created=OLD)) == df.UNRESOLVED


def test_unreadable_numbers_do_not_become_zero():
    assert fate(feats(liq="n/a", created=OLD)) == df.UNRESOLVED
    # A deep pool whose volume cannot be read is not a proven live market…
    assert fate(feats(liq=50_000, vol=None, created=OLD)) == df.UNRESOLVED
    # …and one with real depth but no trades is not a dead one either.
    assert fate(feats(liq=50_000, vol=0, created=OLD)) == df.UNRESOLVED


# ── the sweep ─────────────────────────────────────────────────────────────

class FakeFeed:
    """Answers per address; anything unlisted has no indexed pair."""

    def __init__(self, table):
        self.table = table
        self.asked = []

    async def fetch(self, chain, address):
        self.asked.append(address)
        return self.table.get(address, {})


def sweep(table, addresses, **kw):
    feed = FakeFeed(table)
    out = asyncio.run(df.resolve_fates(addresses, source=feed, now=lambda: NOW, **kw))
    return out, feed


def test_counts_add_up_to_what_was_examined():
    out, _ = sweep({
        '0xa': feats(liq=90_000, vol=500, created=OLD),   # alive
        '0xb': feats(liq=40_000, vol=90, created=OLD),    # alive
        '0xc': feats(liq=5, vol=0, created=OLD),          # dead
        # 0xd unlisted → unresolved
    }, ['0xa', '0xb', '0xc', '0xd'])
    assert out["prior_alive"] == 2
    assert out["prior_dead"] == 1
    assert out["unresolved"] == 1
    assert out["examined"] == 4
    assert out["prior_alive"] + out["prior_dead"] + out["unresolved"] == out["examined"]


def test_no_contracts_reports_nothing_rather_than_zero():
    # Zero alive and zero dead over an empty list would be a measurement of a
    # record that does not exist.
    for empty in ([], None, ['', '  ']):
        out = asyncio.run(df.resolve_fates(empty, now=lambda: NOW))
        assert out["prior_alive"] is None and out["prior_dead"] is None


def test_a_capped_sweep_says_so():
    out, feed = sweep({}, [f'0x{i}' for i in range(40)], max_contracts=5)
    assert out["truncated"] is True
    assert len(feed.asked) == 5, "the cap must bound the requests, not just the report"
    assert "only the most recent" in df.human_readable(out)


def test_this_module_never_supplies_a_rug_count():
    """The whole design in one assertion."""
    out, _ = sweep({'0xa': feats(liq=1, vol=0, created=OLD)}, ['0xa'])
    assert out["prior_dead"] == 1
    assert "prior_rugged" not in out


# ── what the scorer does with it ──────────────────────────────────────────

def _facts(total, alive, dead):
    return {"prior_deployments": total, "prior_alive": alive, "prior_dead": dead,
            "contract_verified": True, "wallet_age_days": 400.0,
            "deployer_supply_pct": 0.02, "concurrent_launches_24h": 0.0,
            "funded_by_mixer": False, "reused_rug_bytecode": False}


def test_a_verified_record_of_survivors_can_finally_be_clean():
    # The first input that could ever produce it: every prior contract checked,
    # every one still trading.
    r = assess_deployer(_facts(4, 4, 0))
    assert r["verdict"] == "clean"
    assert r["outcomes"]["unresolved"] == 0


def test_survivors_nobody_checked_for_failures_are_still_unproven():
    """The trap from this module's own first run, still closed.

    Nine deployments and four confirmed alive scored CLEAN while five fates
    went unread. `prior_dead` being None means nobody counted the bad
    outcomes — which is exactly what `rugged is None` used to mean alone.
    """
    r = assess_deployer({**_facts(9, 4, None), "prior_dead": None})
    assert r["verdict"] == "unproven"


def test_half_the_record_unread_is_still_unproven():
    r = assess_deployer(_facts(10, 3, 1))     # 4 of 10 determined
    assert r["verdict"] == "unproven"


def test_dead_markets_are_a_caution_and_never_a_disqualification():
    """Dying is not stealing, and `known_bad` is what that mistake would print."""
    r = assess_deployer(_facts(4, 0, 4))
    assert r["verdict"] != "known_bad", "an honest failure was scored as theft"
    assert any(c["name"] == "prior_dead_ratio" and c["status"] == "flag"
               for c in r["checks"]), r["checks"]
    # …while a CONFIRMED rug, from a source that can prove one, still is.
    assert assess_deployer({**_facts(4, 3, 0), "prior_rugged": 1})["verdict"] == "known_bad"


def test_one_dead_out_of_many_is_not_the_same_as_one_out_of_one():
    # A ratio, not a count: a builder with 19 live tokens and 1 dead one has
    # not earned the same line as one whose only token died.
    assert not any(c["name"] == "prior_dead_ratio" and c["status"] == "flag"
                   for c in assess_deployer(_facts(20, 19, 1))["checks"])
    assert any(c["name"] == "prior_dead_ratio" and c["status"] == "flag"
               for c in assess_deployer(_facts(1, 0, 1))["checks"])


def test_the_render_counts_the_undetermined_rather_than_hiding_them():
    out, _ = sweep({
        '0xa': feats(liq=90_000, vol=500, created=OLD),
        '0xb': feats(liq=2, vol=0, created=OLD),
    }, ['0xa', '0xb', '0xc', '0xd'])
    text = df.human_readable(out)
    assert 'alive' in text and 'dead' in text
    # A list of survivors with the unreadable ones dropped reads as the whole
    # record. The count travels with it, always.
    assert '2 of 4 could not be determined' in text
