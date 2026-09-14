"""A funding pair is PROPOSED over the caller's own venues, and nothing is placed.

The radar names the delta-neutral direction and the tracker scores the paper
record; neither knows which of a pair's two venues THIS caller has linked,
what each leg could be sized to, or what the round trip costs on that size.
`/arbpair BTC [usd]` is that reading — `bot/core/funding_arb.py` — and it is
a proposal: this build places no pair orders, the card says so, and there is
no button.

THE FIXTURE IS ASYMMETRIC ON PURPOSE. The long leg (bitget) is planted at
$800 USDT and the short leg (hyperliquid) at $300 USDC, with different
funding on each, so a card sized off the wrong leg, or a sentence about the
wrong venue, is a different card. A leg's margin is six-valued and every
non-read state gets its own sentence; a leg READ at $0.00 is a fourth thing
again — a real empty account — and is never "not linked".
"""
from __future__ import annotations

import ast
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.core.arb_tracker import PAPER_NOTIONAL_USD, ROUND_TRIP_FEE_PCT, ArbVerdict
from bot.core.funding_arb import (
    HOURS_PER_YEAR,
    MARGIN_STATES,
    PLACES_NOTHING,
    LegMargin,
    PairProposal,
    breakeven_hours,
    format_pair_card,
    leg_credentials,
    no_pair_card,
    parse_pair_args,
    propose_pair,
    read_leg_margin,
)
from bot.core.funding_radar import FundingRow
from bot.skills.trading_commands import TradingCommands
from tests.test_free_text_obeys_the_role_gate import TRADER

ROOT = Path(__file__).resolve().parent.parent

ROW = FundingRow(base="BTC", rates={"bitget": 3.2, "bybit": 9.0, "hyperliquid": 15.2},
                 spread_apr=12.0, long_venue="bitget", short_venue="hyperliquid")
LONG = LegMargin("bitget", "read", equity_usd=800.0, currency="USDT")
SHORT = LegMargin("hyperliquid", "read", equity_usd=300.0, currency="USDC")
THIN = ArbVerdict("thin", "record too thin — 4 closed entries over 30h held; "
                          "the bar is 10 closed entries and 72h held")
BITGET_FIELDS = {"api_key": "bg-key", "api_secret": "bg-secret", "passphrase": "pp"}
HL_FIELDS = {"wallet_address": "0xabc", "agent_private_key": "hl-agent-key"}


# ── a planted store and a planted venue read ─────────────────────────────────

class _Store:
    def __init__(self, states=None, fields=None, raise_on=()):
        self.states = dict(states or {})
        self.fields = dict(fields or {})
        self.raise_on = set(raise_on)
        self.asked: list = []

    def venue_states(self, uid):
        self.asked.append(("states", uid))
        if "states" in self.raise_on:
            raise RuntimeError("planted store fault: host=vault.internal key=abc")
        return dict(self.states)

    def get_for_venue(self, uid, venue):
        self.asked.append(("get", uid, venue))
        if "get" in self.raise_on:
            raise RuntimeError("planted decrypt fault")
        return self.fields.get(venue)


def _linked_both():
    return _Store(states={"bitget": "readable", "hyperliquid": "readable"},
                  fields={"bitget": BITGET_FIELDS, "hyperliquid": HL_FIELDS})


def _snapshot(answers):
    """A venue read that answers what it is planted with, per venue, and
    records every (venue, fields) it was handed."""
    calls: list = []

    async def snap(venue, fields):
        calls.append((venue, fields))
        a = answers[venue]
        if isinstance(a, Exception):
            raise a
        return a
    snap.calls = calls
    return snap


OK_BG = {"ok": True, "venue": "bitget", "currency": "USDT", "equity_usd": 800.0, "detail": "800.00 USDT total"}
OK_HL = {"ok": True, "venue": "hyperliquid", "currency": "USDC", "equity_usd": 300.0, "detail": "300.00 USDC total"}


# ── the leg read: six values ─────────────────────────────────────────────────

class TestTheLegRead:
    def test_the_six_states_and_a_figure_only_on_read(self):
        assert MARGIN_STATES == ("read", "unpriced", "unreachable", "not_linked", "unreadable", "unavailable")
        with pytest.raises(ValueError):
            LegMargin("bitget", "connected")
        with pytest.raises(ValueError):
            LegMargin("bitget", "not_linked", equity_usd=0.0)
        with pytest.raises(ValueError):
            LegMargin("bitget", "read")
        assert LegMargin("bitget", "read", equity_usd=0.0).equity_usd == 0.0

    def test_leg_credentials_is_three_valued_and_raises_when_the_store_cannot_be_asked(self):
        st = _linked_both()
        assert leg_credentials(st, TRADER, "bitget") == ("readable", BITGET_FIELDS)
        assert leg_credentials(st, TRADER, "bybit") == ("not_linked", None)
        st2 = _Store(states={"bitget": "unreadable"})
        assert leg_credentials(st2, TRADER, "bitget") == ("unreadable", None)
        # A record the store says is readable but hands back empty is unreadable too.
        st3 = _Store(states={"bitget": "readable"}, fields={})
        assert leg_credentials(st3, TRADER, "bitget") == ("unreadable", None)
        with pytest.raises(RuntimeError):
            leg_credentials(None, TRADER, "bitget")
        with pytest.raises(RuntimeError):
            leg_credentials(_Store(raise_on=("states",)), TRADER, "bitget")

    @pytest.mark.asyncio
    async def test_a_readable_venue_is_read_with_its_own_fields(self):
        snap = _snapshot({"bitget": OK_BG, "hyperliquid": OK_HL})
        m = await read_leg_margin("hyperliquid", TRADER, _linked_both(), snapshot=snap)
        assert m == LegMargin("hyperliquid", "read", equity_usd=300.0, currency="USDC")
        assert snap.calls == [("hyperliquid", HL_FIELDS)], "the SHORT leg's own fields, not the long leg's"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("answer,state", [
        ({"ok": True, "venue": "bitget", "currency": "USDT", "equity_usd": None,
          "detail": "authenticated, but no readable USDT balance in the venue's response"}, "unpriced"),
        ({"ok": False, "venue": "bitget", "equity_usd": None,
          "detail": "AuthenticationError: key=abc host=venue.internal"}, "unreachable"),
        ({"ok": True, "venue": "bitget", "currency": "USDT", "equity_usd": float("nan"), "detail": ""}, "unpriced"),
        ({"ok": True, "venue": "bitget", "currency": "USDT", "equity_usd": True, "detail": ""}, "unpriced"),
        (RuntimeError("boom"), "unreachable"),
        ("not a dict", "unreachable"),
    ])
    async def test_a_venue_that_answered_without_a_figure_is_not_an_empty_account(self, answer, state):
        m = await read_leg_margin("bitget", TRADER, _linked_both(), snapshot=_snapshot({"bitget": answer}))
        assert m.state == state and m.equity_usd is None, m

    @pytest.mark.asyncio
    async def test_a_zero_equity_is_a_read(self):
        zero = dict(OK_BG, equity_usd=0.0, detail="0.00 USDT total")
        m = await read_leg_margin("bitget", TRADER, _linked_both(), snapshot=_snapshot({"bitget": zero}))
        assert m.state == "read" and m.equity_usd == 0.0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("store,state", [
        (_Store(states={"bitget": "readable"}, fields={"bitget": BITGET_FIELDS}), "not_linked"),
        (_Store(states={"hyperliquid": "unreadable"}), "unreadable"),
        (_Store(states={"hyperliquid": "readable"}, fields={}), "unreadable"),
        (_Store(raise_on=("states",)), "unavailable"),
        (None, "unavailable"),
    ])
    async def test_the_network_is_never_touched_for_a_venue_the_store_did_not_produce(self, store, state):
        snap = _snapshot({"hyperliquid": OK_HL})
        m = await read_leg_margin("hyperliquid", TRADER, store, snapshot=snap)
        assert m.state == state and m.equity_usd is None
        assert snap.calls == [], "a snapshot for a venue with no produced keys would be a read of nothing"

    @pytest.mark.asyncio
    async def test_the_default_seam_is_the_read_only_balance_snapshot_resolved_at_call_time(self, monkeypatch):
        import bot.core.funding_arb as fa
        seen = []

        async def planted(venue, fields):
            seen.append((venue, fields))
            return OK_BG
        monkeypatch.setattr(fa, "balance_snapshot", planted)
        m = await read_leg_margin("bitget", TRADER, _linked_both())
        assert m.state == "read" and seen == [("bitget", BITGET_FIELDS)]
        # And the seam IS the credential store's read-only snapshot, by identity.
        import bot.core.exchange_credentials as ec
        monkeypatch.undo()
        assert fa.balance_snapshot is ec.balance_snapshot


# ── the proposal: one notional, the smaller leg, the bound named ─────────────

class TestTheProposal:
    def test_the_legs_are_the_rows_and_the_size_is_the_smaller_legs_equity(self):
        p = propose_pair(ROW, LONG, SHORT)
        assert isinstance(p, PairProposal)
        assert (p.long_venue, p.short_venue) == ("bitget", "hyperliquid")
        assert (p.long_apr, p.short_apr, p.spread_apr) == (3.2, 15.2, 12.0)
        assert p.requested_usd == PAPER_NOTIONAL_USD
        assert p.notional_usd == 300.0 and p.sized_by == "hyperliquid", "the SHORT leg bound it"
        assert p.placeable and p.blockers() == ()
        assert abs(p.fee_usd - 300.0 * ROUND_TRIP_FEE_PCT / 100.0) < 1e-9
        assert abs(p.carry_per_day_usd - 300.0 * 0.12 / 365.0) < 1e-9

    def test_a_request_under_both_legs_is_taken_as_requested(self):
        p = propose_pair(ROW, LONG, SHORT, requested_usd=250.0)
        assert p.notional_usd == 250.0 and p.sized_by == "requested"
        p2 = propose_pair(ROW, LONG, SHORT, requested_usd=300.0)
        assert p2.notional_usd == 300.0 and p2.sized_by == "requested", "equal to the cap is not clamped"

    def test_the_long_leg_can_be_the_bound_too(self):
        p = propose_pair(ROW, LegMargin("bitget", "read", equity_usd=120.0, currency="USDT"), SHORT)
        assert p.notional_usd == 120.0 and p.sized_by == "bitget"

    @pytest.mark.parametrize("state", [s for s in MARGIN_STATES if s != "read"])
    def test_a_leg_that_could_not_be_sized_leaves_the_pair_unsized(self, state):
        short = LegMargin("hyperliquid", state)
        p = propose_pair(ROW, LONG, short)
        assert p.notional_usd is None and p.sized_by == "" and not p.placeable
        assert p.fee_usd is None and p.carry_per_day_usd is None
        (why,) = p.blockers()
        assert why.startswith("hyperliquid "), why
        # The long leg's real figure is still known and still printed.
        assert p.long_margin.equity_usd == 800.0

    def test_a_leg_read_at_zero_is_unplaceable_with_its_figure_kept(self):
        p = propose_pair(ROW, LONG, LegMargin("hyperliquid", "read", equity_usd=0.0, currency="USDC"))
        assert p.notional_usd is None and not p.placeable
        assert p.blockers() == ("hyperliquid equity reads $0.00",)

    def test_a_flat_spread_never_breaks_even_and_is_not_placeable(self):
        flat = FundingRow(base="BTC", rates={"bitget": 5.0, "bybit": 5.0}, spread_apr=0.0,
                          long_venue="bitget", short_venue="bybit")
        p = propose_pair(flat, LONG, LegMargin("bybit", "read", equity_usd=500.0, currency="USDT"))
        assert p.notional_usd == 500.0, "sized, but"
        assert p.breakeven_hours is None and p.carry_per_day_usd is None and not p.placeable
        assert p.blockers() == ("no positive spread to collect",)

    def test_breakeven_is_the_fee_over_the_spread_in_hours(self):
        assert abs(breakeven_hours(12.0) - ROUND_TRIP_FEE_PCT / 12.0 * HOURS_PER_YEAR) < 1e-9
        assert abs(breakeven_hours(12.0, 0.24) - 175.2) < 1e-9
        assert breakeven_hours(0.0) is None and breakeven_hours(-3.0) is None
        assert breakeven_hours(float("nan")) is None

    def test_the_reads_have_to_be_the_rows_legs(self):
        with pytest.raises(ValueError):
            propose_pair(ROW, SHORT, LONG)
        with pytest.raises(ValueError):
            propose_pair(ROW, LONG, SHORT, requested_usd=0.0)
        with pytest.raises(ValueError):
            propose_pair(ROW, LONG, SHORT, requested_usd=float("inf"))


# ── the words the command takes ──────────────────────────────────────────────

class TestTheArguments:
    @pytest.mark.parametrize("args,expect", [
        (["btc"], ("BTC", PAPER_NOTIONAL_USD)),
        (["BTC/USDT", "500"], ("BTC", 500.0)),
        (["btcusdt"], ("BTC", PAPER_NOTIONAL_USD)),
        (["BTC/USDT:USDT", "$1,250.50"], ("BTC", 1250.5)),
        (["eth", "12345.67"], ("ETH", 12345.67)),
    ])
    def test_a_coin_and_an_optional_size(self, args, expect):
        assert parse_pair_args(args) == expect

    @pytest.mark.parametrize("args", [[], ["<b>"], ["B"], ["a-very-long-name-here"],
                                      ["BTC", "abc"], ["BTC", "-5"], ["BTC", "0"], ["BTC", "nan"],
                                      ["BTC", "inf"], ["BTC", "99999999999"]])
    def test_anything_else_is_a_sentence_not_a_card(self, args):
        out = parse_pair_args(args)
        assert isinstance(out, str) and "/arbpair" in out
        assert "<b>" not in out


# ── the card ─────────────────────────────────────────────────────────────────

class TestTheCard:
    def test_a_sized_pair_prints_both_legs_the_bound_the_fee_bar_and_the_verdict(self):
        card = format_pair_card(propose_pair(ROW, LONG, SHORT), THIN)
        assert "Funding pair — BTC" in card and "places nothing" in card
        assert "Long  <b>bitget</b> · funding <code>+3.2%/yr</code>" in card
        assert "Short <b>hyperliquid</b> · funding <code>+15.2%/yr</code>" in card
        assert "Spread <code>12.0%/yr</code>" in card
        assert ("Size: <code>$300.00</code> per leg at 1x (sized to hyperliquid's equity — "
                "you asked for $1,000.00)") in card
        assert "Fee bar: <code>0.24%</code> round trip = <code>$0.72</code> on this size" in card
        assert "break-even hold ≈ <b>7.3 days (175 h)</b>" in card
        assert "Carry at today's spread ≈ <code>$0.10/day</code>" in card
        assert "• <b>bitget</b>: equity <code>$800.00</code> USDT" in card
        assert "• <b>hyperliquid</b>: equity <code>$300.00</code> USDC" in card
        assert "Evidence (the paper record, /arb): 🟡 <b>record too thin" in card
        assert "✅ Could be placed at <code>$300.00</code> per leg" in card
        assert card.endswith(f"<i>{PLACES_NOTHING}</i>")
        assert "Nothing was placed, nothing is armed" in card
        assert "Confirm" not in card, "no button, no word inviting one"

    def test_a_request_that_fits_says_as_requested(self):
        card = format_pair_card(propose_pair(ROW, LONG, SHORT, requested_usd=250.0), THIN)
        assert "Size: <code>$250.00</code> per leg at 1x (as requested)" in card
        assert "you asked for" not in card

    @pytest.mark.parametrize("state,words", [
        ("not_linked", "not linked — <code>/connect hyperliquid</code> links it"),
        ("unreadable", "keys stored but could not be decrypted"),
        ("unreachable", "did not answer the balance read — not an empty account"),
        ("unpriced", "authenticated, but no readable USDC balance"),
        ("unavailable", "could not be asked — the credential store did not answer"),
    ])
    def test_each_absence_gets_its_own_sentence_and_the_pair_is_unsized(self, state, words):
        short = LegMargin("hyperliquid", state, currency="USDC" if state == "unpriced" else "")
        card = format_pair_card(propose_pair(ROW, LONG, short), THIN)
        assert f"• <b>hyperliquid</b>: {words}" in card, card
        assert "Size: unsized — you asked for <code>$1,000.00</code> per leg" in card
        assert "⛔ Cannot be placed as proposed: hyperliquid " in card
        assert "$300.00" not in card, "no figure for a leg nobody read"
        assert "• <b>bitget</b>: equity <code>$800.00</code> USDT" in card, "the read leg keeps its figure"
        # The fee bar and break-even are still facts about the spread.
        assert "break-even hold ≈ <b>7.3 days (175 h)</b>" in card
        assert card.endswith(f"<i>{PLACES_NOTHING}</i>")

    def test_a_zero_read_is_not_a_missing_link(self):
        zero = LegMargin("hyperliquid", "read", equity_usd=0.0, currency="USDC")
        card = format_pair_card(propose_pair(ROW, LONG, zero), THIN)
        assert "• <b>hyperliquid</b>: equity <code>$0.00</code> USDC" in card
        assert "⛔ Cannot be placed as proposed: hyperliquid equity reads $0.00." in card
        assert "not linked" not in card

    def test_a_flat_spread_says_never(self):
        flat = FundingRow(base="BTC", rates={"bitget": 5.0, "bybit": 5.0}, spread_apr=0.0,
                          long_venue="bitget", short_venue="bybit")
        bybit = LegMargin("bybit", "read", equity_usd=500.0, currency="USDT")
        card = format_pair_card(propose_pair(flat, LONG, bybit), THIN)
        assert "<b>never</b> at today's spread" in card
        assert "⛔ Cannot be placed as proposed: no positive spread to collect." in card
        assert "Carry at today's spread" not in card

    @pytest.mark.parametrize("verdict,icon", [
        (ArbVerdict("survives", "survives fees — mean net $1.10 per entry"), "🟢"),
        (ArbVerdict("does_not", "does not survive fees — mean net $-0.40 per entry"), "🔴"),
        (THIN, "🟡"),
        (ArbVerdict("unread", "could not read the record: OSError"), "🔴"),
    ])
    def test_the_evidence_line_is_the_records_own_verdict(self, verdict, icon):
        card = format_pair_card(propose_pair(ROW, LONG, SHORT), verdict)
        assert f"Evidence (the paper record, /arb): {icon} <b>{verdict.reason}</b>" in card

    def test_the_venues_rejection_text_never_reaches_the_card(self):
        short = LegMargin("hyperliquid", "unreachable", detail="AuthenticationError: key=abc host=venue.internal")
        card = format_pair_card(propose_pair(ROW, LONG, short), THIN)
        assert "venue.internal" not in card and "key=abc" not in card and "AuthenticationError" not in card

    def test_the_no_pair_card_claims_nothing_and_names_the_two_reasons(self):
        card = no_pair_card("DOGE")
        assert "Funding pair — DOGE" in card and "fewer than two" in card and "did not answer" in card
        assert card.endswith("Nothing was placed.")

    def test_hours_under_two_days_print_as_hours(self):
        wide = FundingRow(base="BTC", rates={"bitget": 0.0, "hyperliquid": 120.0}, spread_apr=120.0,
                          long_venue="bitget", short_venue="hyperliquid")
        card = format_pair_card(propose_pair(wide, LONG, SHORT), THIN)
        assert "break-even hold ≈ <b>17.5 h</b>" in card


# ── the command, on a bare host ──────────────────────────────────────────────

def _host(monkeypatch, *, store=None, rows=None, snapshot=None, verdict=THIN, guard_ok=True, uid=TRADER):
    """Bind the real command onto a bare host: the radar, the store, the
    venue read and the record are all planted at the module seams the
    command imports from."""
    import bot.core.arb_tracker as at
    import bot.core.exchange_credentials as ec
    import bot.core.funding_arb as fa
    import bot.core.funding_radar as fr

    radar_calls: list = []

    def build_comparison(bases, fetchers=None):
        radar_calls.append(list(bases))
        return list(rows if rows is not None else [ROW])
    monkeypatch.setattr(fr, "build_comparison", build_comparison)
    monkeypatch.setattr(at, "arb_reading", lambda path=None: ([], verdict))
    if store == "raises":
        def _boom():
            raise RuntimeError("store host=vault.internal")
        monkeypatch.setattr(ec, "get_credential_store", _boom)
    else:
        monkeypatch.setattr(ec, "get_credential_store", lambda: store if store is not None else _linked_both())
    snap = snapshot if snapshot is not None else _snapshot({"bitget": OK_BG, "hyperliquid": OK_HL})
    monkeypatch.setattr(fa, "balance_snapshot", snap)

    sent: list = []

    async def _send(update, text, reply_markup=None, edit=False):
        sent.append((text, reply_markup))
    guarded: list = []

    async def _guard(update, command="", ctx=None):
        guarded.append(command)
        return guard_ok
    host = SimpleNamespace(sent=sent, guarded=guarded, radar_calls=radar_calls, snap=snap,
                           _send=_send, _guard=_guard, _get_tg_id=lambda update: uid,
                           _lang=lambda update: "en")
    host._cmd_arbpair = TradingCommands._cmd_arbpair.__get__(host)
    return host


def _update(args=()):
    return SimpleNamespace(), SimpleNamespace(args=list(args))


class TestTheCommand:
    @pytest.mark.asyncio
    async def test_a_linked_trader_gets_the_pair_sized_over_their_own_two_venues(self, monkeypatch):
        host = _host(monkeypatch)
        await host._cmd_arbpair(*_update(["btc"]))
        assert host.guarded == ["trade"], "the role gate runs first"
        assert host.radar_calls == [["BTC"]]
        assert sorted(v for v, _ in host.snap.calls) == ["bitget", "hyperliquid"], \
            "exactly the two legs are read, each with its own fields"
        assert dict(host.snap.calls) == {"bitget": BITGET_FIELDS, "hyperliquid": HL_FIELDS}
        card, markup = host.sent[-1]
        assert markup is None, "no button: nothing to confirm on a card that places nothing"
        assert "Size: <code>$300.00</code> per leg at 1x (sized to hyperliquid's equity" in card
        assert "• <b>bitget</b>: equity <code>$800.00</code> USDT" in card
        assert "Evidence (the paper record, /arb): 🟡 <b>record too thin" in card
        assert card.endswith(f"<i>{PLACES_NOTHING}</i>")

    @pytest.mark.asyncio
    async def test_the_size_argument_rides_through(self, monkeypatch):
        host = _host(monkeypatch)
        await host._cmd_arbpair(*_update(["BTC", "250"]))
        assert "Size: <code>$250.00</code> per leg at 1x (as requested)" in host.sent[-1][0]

    @pytest.mark.asyncio
    async def test_a_refused_caller_gets_nothing_and_nothing_is_read(self, monkeypatch):
        host = _host(monkeypatch, guard_ok=False)
        await host._cmd_arbpair(*_update(["btc"]))
        assert host.guarded == ["trade"] and host.sent == [] and host.radar_calls == [] and host.snap.calls == []

    @pytest.mark.asyncio
    async def test_no_argument_is_the_usage_line_and_no_read(self, monkeypatch):
        host = _host(monkeypatch)
        await host._cmd_arbpair(*_update([]))
        assert host.sent[-1][0].startswith("⚠️ Usage: /arbpair BTC [usd]")
        assert host.radar_calls == [] and host.snap.calls == []

    @pytest.mark.asyncio
    async def test_a_coin_the_radar_cannot_pair_gets_the_no_pair_card_and_no_venue_read(self, monkeypatch):
        host = _host(monkeypatch, rows=[])
        await host._cmd_arbpair(*_update(["doge"]))
        assert host.sent[-1] == (no_pair_card("DOGE"), None)
        assert host.snap.calls == []

    @pytest.mark.asyncio
    async def test_a_row_for_another_coin_is_not_this_coins_pair(self, monkeypatch):
        # The radar is asked for ONE base; a row that came back for a different
        # one (a stub, a cache, a future batch read) must not be dressed as ETH.
        host = _host(monkeypatch, rows=[ROW])
        await host._cmd_arbpair(*_update(["eth"]))
        assert host.radar_calls == [["ETH"]]
        assert host.sent[-1] == (no_pair_card("ETH"), None) and host.snap.calls == []

    @pytest.mark.asyncio
    async def test_an_unlinked_caller_sees_both_legs_named_and_no_size(self, monkeypatch):
        host = _host(monkeypatch, store=_Store())
        await host._cmd_arbpair(*_update(["btc"]))
        card = host.sent[-1][0]
        assert host.snap.calls == [], "no keys, no network"
        assert "• <b>bitget</b>: not linked — <code>/connect bitget</code> links it" in card
        assert "• <b>hyperliquid</b>: not linked — <code>/connect hyperliquid</code> links it" in card
        assert "⛔ Cannot be placed as proposed: bitget is not linked; hyperliquid is not linked." in card
        assert "Spread <code>12.0%/yr</code>" in card, "the public half of the card is still a reading"

    @pytest.mark.asyncio
    async def test_a_store_that_cannot_be_asked_is_not_a_caller_with_no_links(self, monkeypatch):
        host = _host(monkeypatch, store="raises")
        await host._cmd_arbpair(*_update(["btc"]))
        card = host.sent[-1][0]
        assert host.snap.calls == []
        for venue in ("bitget", "hyperliquid"):
            assert f"• <b>{venue}</b>: could not be asked — the credential store did not answer" in card
        assert "⛔ Cannot be placed as proposed: bitget could not be asked" in card
        assert "not linked" not in card and "vault.internal" not in card

    @pytest.mark.asyncio
    async def test_one_dead_venue_leaves_the_pair_unsized_and_the_other_leg_read(self, monkeypatch):
        dead = {"ok": False, "venue": "hyperliquid", "equity_usd": None,
                "detail": "NetworkError: host=api.hyperliquid.internal"}
        host = _host(monkeypatch, snapshot=_snapshot({"bitget": OK_BG, "hyperliquid": dead}))
        await host._cmd_arbpair(*_update(["btc"]))
        card = host.sent[-1][0]
        assert "• <b>bitget</b>: equity <code>$800.00</code> USDT" in card
        assert "• <b>hyperliquid</b>: did not answer the balance read — not an empty account" in card
        assert "Size: unsized" in card and "hyperliquid.internal" not in card

    @pytest.mark.asyncio
    async def test_a_radar_that_raises_is_a_failure_sentence_that_claims_nothing(self, monkeypatch):
        host = _host(monkeypatch)
        import bot.core.funding_radar as fr

        def boom(bases, fetchers=None):
            raise RuntimeError("radar down")
        monkeypatch.setattr(fr, "build_comparison", boom)
        await host._cmd_arbpair(*_update(["btc"]))
        assert host.sent[-1][0].endswith("Nothing was placed.") and "could not be built" in host.sent[-1][0]


# ── the wiring ───────────────────────────────────────────────────────────────

class TestTheWiring:
    def test_the_command_is_guarded_with_trade_and_recorded(self):
        tree = ast.parse((ROOT / "bot/skills/trading_commands.py").read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == "_cmd_arbpair")
        decos = [ast.unparse(d) for d in fn.decorator_list]
        assert decos == ["guard('trade')"], decos
        names = (ROOT / "tests/guarded_commands_baseline.txt").read_text().split()
        assert "_cmd_arbpair" in names

    def test_it_is_registered_and_catalogued_for_users_as_a_proposal(self):
        from bot.skills.command_catalog import all_entries
        title, audience, desc = all_entries()["arbpair"]
        assert title == "📈 Trading" and audience == "user"
        assert "places nothing" in desc and "/arbpair BTC" in desc
        src = (ROOT / "bot/skills/telegram_handler.py").read_text(encoding="utf-8")
        assert '("arbpair", self._cmd_arbpair)' in src

    def test_the_card_carries_no_dollar_figure_the_caller_did_not_own(self):
        # Every dollar on the card is the caller's own equity or derived from
        # it — a private surface — and the PAPER notional appears only as the
        # requested default, never as a balance.
        import re
        card = format_pair_card(propose_pair(ROW, LONG, SHORT), THIN)
        figures = set(re.findall(r"\$([\d,]+\.\d{2})", card))
        assert figures == {"300.00", "1,000.00", "0.72", "0.10", "800.00"}, figures
        assert "you asked for $1,000.00" in card, "the paper notional is the REQUESTED default, named as such"
        assert not math.isnan(PAPER_NOTIONAL_USD)
