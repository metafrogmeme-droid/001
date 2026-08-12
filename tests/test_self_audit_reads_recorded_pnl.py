"""The nightly self-audit graded a live account off a field that does not exist.

`gather_evidence` read every closed trade with

    "net_pnl": round(float(getattr(t, "net_pnl", 0) or 0), 3)

against `live_executor._closed_trades`, a `list[LivePosition]`. `LivePosition`
carries `pnl_usd` and `gross_pnl` and has **no `net_pnl` at all**, so that
getattr was not a defensive default — it was the constant 0, on every row, for
every trade the bot has ever closed. The summary computed from those rows then
published

    win_rate : 0.0        # not None — a measured-looking zero
    net_pnl  : 0.0
    pf       : None

for a book that in the driving case below is 3 wins, 2 losses and +$200.

`win_rate: 0.0` is the worst possible spelling of it. `None` would have said
"not measured"; `0.0` says "nothing won", and the shape is the one the repo
names first: `getattr(o, "pnl", 0)` — absent field is zero.

It is not only a display. The evidence dict goes verbatim into the prompt that
proposes environment-flag changes to a live perpetuals bot, so a fabricated
0% win rate was the input to an automated change proposer, and `[]` on the
failure path told that model the bot had closed no trades at all.

The fix routes every read through `bot.utils.win_rate`, which already existed
for exactly this and already tries `pnl_usd` first. Per the audit's own note,
the cure is the shared reader and never an inlined `is None` check — that
recreates the divergence this module was written to end.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from bot.core.live_executor import LivePosition
from bot.core.self_audit import SelfAudit


def _pos(pnl_usd, *, symbol="BTC/USDT:USDT", gross=None, reason="tp"):
    """A closed LivePosition. `gross` is the red herring — see the test."""
    return LivePosition(
        trade_id=f"t-{symbol}-{pnl_usd}",
        symbol=symbol,
        direction="LONG",
        entry_price=100.0,
        quantity=1.0,
        cost_usd=100.0,
        stop_loss=90.0,
        take_profit=120.0,
        leverage=1,
        is_spot=False,
        opened_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        closed_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        close_price=110.0,
        pnl_usd=pnl_usd,
        status="closed",
        strategy_type="breakout",
        gross_pnl=gross,
        close_reason=reason,
    )


class _Engine:
    def __init__(self, closed):
        self.live_executor = type("_Ex", (), {"_closed_trades": closed})()


def _summary(closed):
    return SelfAudit().gather_evidence(_Engine(closed))["summary"]


# ── the field that was never there ───────────────────────────────────

def test_live_position_has_no_net_pnl_field():
    """The premise, pinned. If `net_pnl` is ever added to LivePosition this
    test fails loudly rather than letting the old getattr quietly start
    working and hiding what happened for the five months it did not."""
    names = {f.name for f in dataclasses.fields(LivePosition)}
    assert "net_pnl" not in names, (
        "LivePosition gained a net_pnl field — revisit the readers, and note "
        "that getattr(t, 'net_pnl', 0) was silently returning 0 until now")
    assert "pnl_usd" in names, "the field the audit must read"


# ── the driving case ─────────────────────────────────────────────────

def test_a_profitable_record_is_not_reported_as_zero():
    """3 wins, 2 losses, +$200 net. Before the fix: 0.0 / 0.0 / None."""
    closed = [_pos(150.0), _pos(120.0), _pos(30.0),
              _pos(-40.0, reason="sl"), _pos(-60.0, reason="sl")]
    s = _summary(closed)
    assert s["n"] == 5
    assert s["scored"] == 5
    assert s["unpriced"] == 0
    assert s["win_rate"] == 0.6
    assert s["net_pnl"] == 200.0
    assert s["pf"] == 3.0            # 300 won / 100 lost


def test_the_gross_pnl_is_not_mistaken_for_the_net():
    """Red herring: a set whose GROSS is positive and whose NET is negative.

    `LivePosition` carries both, and a reader that grabs whichever field it
    finds first would publish a winning night for a losing one. The audit
    reports the net, so this record is a loss.
    """
    closed = [_pos(-30.0, gross=90.0, reason="sl"),
              _pos(-25.0, gross=80.0, reason="sl")]
    s = _summary(closed)
    assert s["net_pnl"] == -55.0, "gross_pnl leaked into the net"
    assert s["win_rate"] == 0.0, "both closes lost; this zero IS measured"


# ── absence vs. measurement ──────────────────────────────────────────

def test_an_unpriced_close_leaves_both_sides_of_the_ratio():
    closed = [_pos(100.0), _pos(-50.0, reason="sl"), _pos(None, reason="manual")]
    s = _summary(closed)
    assert s["n"] == 3
    assert s["scored"] == 2
    assert s["unpriced"] == 1
    assert s["win_rate"] == 0.5, "the unpriced close was scored as a loss"
    assert s["net_pnl"] == 50.0, "the unpriced close was summed as a zero"


def test_nothing_priceable_is_none_and_never_zero():
    """The distinction the whole file is about. Asserted as `is None` AND as
    `!= 0`, because `0.0 == False == None`-adjacent slips are how this class
    of bug survives a test that only checks falsiness."""
    s = _summary([_pos(None), _pos(None, reason="manual")])
    assert s["n"] == 2
    assert s["scored"] == 0
    assert s["unpriced"] == 2
    for key in ("win_rate", "net_pnl", "pf"):
        assert s[key] is None, f"{key} was {s[key]!r}; nothing was measured"


def test_an_empty_book_reports_no_measurement():
    s = _summary([])
    assert s["n"] == 0
    assert s["win_rate"] is None
    assert s["net_pnl"] is None, "sum(()) is 0.0, and printing it claims a flat book"


def test_no_losing_trade_yet_is_not_a_profit_factor_of_zero():
    """PF is None with no losses — 'no losing round-trip yet', not 'every
    trade lost'. The one figure where 0.0 and None are furthest apart."""
    s = _summary([_pos(10.0), _pos(20.0)])
    assert s["pf"] is None
    assert s["win_rate"] == 1.0


# ── the rows the model reads ─────────────────────────────────────────

def test_evidence_rows_carry_null_for_an_unpriced_close():
    """The per-trade rows go into the prompt too. A 0 there is a claim about
    that individual trade, independent of the summary above it."""
    ev = SelfAudit().gather_evidence(_Engine([_pos(None), _pos(42.0)]))
    rows = ev["closed_trades"]
    assert [r["net_pnl"] for r in rows] == [None, 42.0]


def test_an_unreadable_record_is_not_an_empty_one():
    """`closed_trades = []` said "this bot has closed no trades" — a
    confident false statement about a live account, fed to a change proposer."""
    class _Exploding:
        @property
        def _closed_trades(self):
            raise RuntimeError("state file locked")

    ev = SelfAudit().gather_evidence(type("_E", (), {"live_executor": _Exploding()})())
    assert ev["closed_trades"] is None, "an unreadable record rendered as empty"
    assert (ev.get("summary") or {}).get("error"), "no error marker on the summary"


def test_the_prompt_tells_the_model_null_is_not_zero():
    """The evidence now carries nulls, so the instruction that reads them has
    to travel with it. Pinned because the two are only correct together: nulls
    without the instruction invite the model to average them in as zeros."""
    from bot.core.self_audit import _SYSTEM_PROMPT
    assert "null" in _SYSTEM_PROMPT.lower()
    assert "not measured" in _SYSTEM_PROMPT.lower()


# ── what the card says ───────────────────────────────────────────────

def _report(summary):
    return SelfAudit.render_report({"summary": summary}, [], {}, "alts_1h")


def test_the_report_says_unreadable_rather_than_going_quiet():
    text = _report({"error": "closed_trade_record_unreadable"})
    assert "could not be read" in text
    for forbidden in ("0%", "$0.00", "PF 0"):
        assert forbidden not in text, f"the card printed {forbidden!r}"


def test_the_report_prints_a_dash_not_a_zero_for_an_unmeasured_net():
    text = _report({"n": 3, "scored": 0, "unpriced": 3,
                    "win_rate": None, "net_pnl": None, "pf": None})
    assert "$0.00" not in text, "an unmeasured net rendered as a flat book"
    assert "net —" in text
    assert "win —" in text
    assert "3 do not" in text, "the coverage of the window was not disclosed"


def test_the_report_still_prints_a_real_record():
    text = _report({"n": 5, "scored": 5, "unpriced": 0,
                    "win_rate": 0.6, "net_pnl": 200.0, "pf": 3.0})
    assert "5 closes" in text and "win 60%" in text
    assert "$200.00" in text and "PF 3.0" in text
    assert "do not" not in text, "a coverage caveat printed on a complete window"


@pytest.mark.parametrize("net", [0.0, -0.0])
def test_a_genuinely_flat_book_still_prints_its_zero(net):
    """The other half of the rule: a measured 0.0 is a real result and must
    NOT be hidden. A fix that renders every zero as '—' is the same defect
    facing the other way."""
    text = _report({"n": 2, "scored": 2, "unpriced": 0,
                    "win_rate": 0.0, "net_pnl": net, "pf": None})
    assert "$0.00" in text, "a measured break-even was suppressed"
    assert "win 0%" in text
