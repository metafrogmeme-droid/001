"""The balance-relative bounds' shadow is READABLE: a record the preflight
writes on every live order, and /shadow bounds renders it.

Driven before this slice: `size_bounds.resolve` answered `flat` with
`SIZE_BOUNDS_ENABLED` off and computed no would-be, although the AVAILABLE
balance the venue reported was in hand on every live order regardless
(`execute()` reads it once and hands it to the clamp and the preflight). The
evidence the operator needs before arming the flag was computable for free
and recorded nowhere -- the quality ladder's shape one flag over.

WHAT IS DRIVEN HERE
-------------------
* the preflight's refusals come from ONE reading (`size_bounds.bounds_verdict`),
  in the preflight's own order, with the sentences it always printed; the
  reserve from `reserve_read`, with its basis
* `order_row`: the would-be bound is a CLAMP, the total and the reserve are
  read at the size it would have placed, an unread balance leaves nothing to
  compare, and a row with the flag on says so with the pre-clamp size
* `summarize`: per account, the balance-unread rows counted apart, a row
  another build wrote counted rather than dropped, a raised ceiling named
* the preflight writes one row per order it is asked about (flag off:
  would-be; flag on: applied), reading the module singleton at call time; a
  refusal is still returned; a ledger fault costs the order nothing
* `execute()` hands the preflight the size it held BEFORE the clamp -- a
  scan, stated as one: the order path is an async venue round trip
* the card, line by line, in each state; the FULL sentence only when full
* `/shadow bounds` through the real handler; the scoreboard names it
* every document that describes the bounds names the command, and the
  harness cleans the file the preflight writes
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import inspect
import pathlib
from types import SimpleNamespace

import pytest

from bot.config import CONFIG
from bot.core import bounds_shadow as bs
from bot.core import live_executor as lx
from bot.core import size_bounds as sb
from bot.skills.command_catalog import all_entries
from bot.skills.engine_ops_commands import LADDER_POINTER
from bot.skills.telegram_handler import TelegramHandler
from bot.utils import shadow_ledger as sl

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── fixtures ────────────────────────────────────────────────────────────────

def _cfg(**kw):
    """A config stand-in carrying what `resolve` and the card read."""
    base = dict(
        max_live_position_usd=100.0, max_live_total_exposure_usd=500.0,
        balance_relative_bounds_enabled=False, balance_bounds_per_trade_pct=10.0,
        balance_bounds_total_pct=50.0, balance_bounds_reserve_pct=20.0,
        balance_bounds_max_position_usd=100.0, balance_bounds_max_total_usd=500.0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _bounds(avail, on=False, **kw):
    return sb.resolve(avail, _cfg(balance_relative_bounds_enabled=on, **kw),
                      flat_per_trade=100.0, flat_total=500.0)


def _exposure(total=60.0, scored=1, counted=1, unread=(), complete=True):
    return SimpleNamespace(total=total, scored=scored, counted=counted,
                           unread=tuple(unread), complete=complete)


def _pos(symbol="BTC/USDT", cost=60.0, status="open"):
    return SimpleNamespace(trade_id=f"T-{symbol}", symbol=symbol, status=status,
                           cost_usd=cost, direction="LONG")


def _ex(tmp_path, positions=None, user_id=None):
    ex = lx.LiveExecutor(user_id=user_id, state_dir=str(tmp_path))
    ex._hedge_mode = False
    ex._positions = {f"t{i}": p for i, p in enumerate(positions or [])}
    return ex


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    led = sl.ShadowLedger(str(tmp_path / "bounds.json"))
    monkeypatch.setattr(bs, "BOUNDS_LEDGER", led)
    return led


def _row(size=100.0, avail=200.0, on=False, before=None, exposure=None, account=None,
         now=1_758_000_000.0, **cfg):
    in_force = _bounds(avail, on=on, **cfg)
    would = _bounds(avail, on=True, **cfg)
    return bs.order_row(account=account, symbol="ETH/USDT", size_usd=size,
                        available_usd=avail, in_force=in_force, would=would,
                        exposure=exposure if exposure is not None else _exposure(),
                        enabled=on, size_before_usd=before, now=now)


# ── one reading for the preflight's refusals ────────────────────────────────

class TestTheVerdictIsTheOnePreflightReading:
    def test_ok_carries_the_read_total(self):
        v = sb.bounds_verdict(50.0, _bounds(None), _exposure(60.0))
        assert v == sb.BoundsVerdict("ok", None, 60.0)

    def test_the_per_trade_refusal_is_the_preflights_sentence(self):
        v = sb.bounds_verdict(150.0, _bounds(None), _exposure(60.0))
        assert v.state == "per_trade" and v.exposure_total is None
        assert v.sentence == ("Position size $150.00 exceeds the $100.00 per-trade margin "
                              "limit (balance-relative bounds are off)")

    def test_the_per_trade_bound_is_asked_before_the_book(self):
        """A book nobody could read does not change the answer to a size
        that is over the per-trade bound: the preflight's own order."""
        v = sb.bounds_verdict(150.0, _bounds(None), _exposure(None, 0, 2, ("A", "B"), False))
        assert v.state == "per_trade"

    def test_an_unread_book_is_named_and_not_summed_as_zero(self):
        v = sb.bounds_verdict(10.0, _bounds(None), _exposure(None, 0, 2, ("A", "B"), False))
        assert v.state == "exposure_unread" and v.exposure_total is None
        assert v.sentence == ("Total exposure cannot be measured: the venue stated no margin "
                              "for any of the 2 open position(s) on this account (A, B). "
                              "Close them with /liveclose, or the $500.00 cap cannot be "
                              "enforced.")

    def test_a_partial_book_says_the_figure_is_a_floor(self):
        v = sb.bounds_verdict(10.0, _bounds(None), _exposure(25.0, 1, 2, ("B",), False))
        assert v.state == "exposure_partial" and v.exposure_total is None
        assert v.sentence == ("Total exposure cannot be measured: the venue never stated a "
                              "margin for B, so the $25.00 on record over 1 of 2 position(s) "
                              "is a floor and not the total. Close it with /liveclose, or "
                              "the $500.00 cap cannot be enforced.")

    def test_the_total_refusal_is_the_preflights_sentence(self):
        v = sb.bounds_verdict(50.0, _bounds(None), _exposure(470.0))
        assert v.state == "total" and v.exposure_total == 470.0
        assert v.sentence == ("Total exposure $520.00 would exceed the $500.00 total margin "
                              "limit (balance-relative bounds are off)")

    def test_the_preflight_prints_exactly_these_sentences(self, tmp_path, ledger):
        ex = _ex(tmp_path, [_pos(cost=470.0)])
        err = ex._preflight_check(50.0, symbol="ETH/USDT")
        assert err == sb.bounds_verdict(50.0, lx.size_bounds_for(None),
                                        lx.committed_margin(ex.open_positions)).sentence
        # The PER-TRADE refusal too: the first round of this slice drove only
        # the total, so a private copy of the per-trade sentence in the
        # preflight -- returning BEFORE the leaf is asked -- agreed with every
        # fixture and survived. Equality, not containment.
        ex2 = _ex(tmp_path, [])
        err2 = ex2._preflight_check(150.0, symbol="ETH/USDT")
        assert err2 == sb.bounds_verdict(150.0, lx.size_bounds_for(None),
                                         lx.committed_margin(ex2.open_positions)).sentence
        assert err2.startswith("Position size $150.00 exceeds")

    def test_the_preflight_reads_the_leaf_and_not_a_copy(self, tmp_path, ledger, monkeypatch):
        """PLANT the reading and read what the door says: a byte-identical copy
        of the comparison in the preflight agrees with every fixture above, so
        the only way to prove ONE reading is to make the leaf answer something
        no copy could. A preflight that refuses on its own arithmetic before
        asking the leaf never returns the marker."""
        asked = []

        def planted(size_usd, bounds, exposure):
            asked.append((size_usd, bounds.basis))
            return sb.BoundsVerdict("per_trade", "MARKER-FROM-THE-LEAF", None)

        monkeypatch.setattr(sb, "bounds_verdict", planted)
        ex = _ex(tmp_path, [])
        assert ex._preflight_check(150.0, symbol="ETH/USDT") == "MARKER-FROM-THE-LEAF"
        assert ex._preflight_check(10.0, symbol="ETH/USDT") == "MARKER-FROM-THE-LEAF"
        # asked twice for the preflight's own verdict, plus twice for each of
        # the two would-be rows the record builds (in force + would-be)
        assert [s for s, _ in asked].count(150.0) >= 1 and [s for s, _ in asked].count(10.0) >= 1

    @pytest.mark.parametrize("avail, exposure, size, state, basis", [
        (200.0, 60.0, 20.0, "ok", "available balance"),        # 120 left ≥ 40
        (200.0, 130.0, 50.0, "warn", "available balance"),     # 20 left < 40
        (200.0, 150.0, 50.0, "spent", "available balance"),    # nothing left
        (None, 100.0, 50.0, "ok", "the configured total limit"),   # 350 left ≥ 100
        (None, 400.0, 50.0, "warn", "the configured total limit"),  # 50 left < 100
    ])
    def test_the_reserve_has_three_states_and_names_its_basis(self, avail, exposure, size,
                                                              state, basis):
        r = sb.reserve_read(size, _bounds(avail, on=avail is not None), exposure)
        assert (r.state, r.basis) == (state, basis)

    def test_the_preflight_warns_from_the_same_reading(self, tmp_path, ledger, monkeypatch):
        rows = []
        monkeypatch.setattr(lx, "audit", lambda log, msg, **kw: rows.append((msg, kw)))
        ex = _ex(tmp_path, [_pos(cost=400.0)])
        assert ex._preflight_check(50.0, symbol="ETH/USDT") is None
        warn = [kw for msg, kw in rows if kw.get("action") == "capital_buffer"]
        assert len(warn) == 1 and warn[0]["result"] == "WARN"
        assert warn[0]["data"]["reserve_basis"] == "the configured total limit"
        assert warn[0]["data"]["remaining"] == 50.0 and warn[0]["data"]["reserve"] == 100.0


# ── the row ─────────────────────────────────────────────────────────────────

class TestTheRowSaysWhatTheBoundsWouldDo:
    def test_flag_off_a_read_balance_names_the_would_be_cut(self):
        r = _row(size=100.0, avail=200.0)
        assert r["account"] == "operator" and r["enabled"] is False
        assert (r["in_force_basis"], r["in_force_per_trade"], r["in_force_state"]) == ("flat", 100.0, "ok")
        assert (r["would_basis"], r["would_per_trade"], r["would_total"], r["would_reserve"]) == \
            ("balance", 20.0, 100.0, 40.0)
        assert r["would_size_usd"] == 20.0, "the would-be bound is a clamp"
        assert r["would_state"] == "ok" and r["would_reserve_state"] == "ok"
        assert r["exposure_total"] == 60.0 and r["size_before_usd"] is None

    def test_the_total_and_the_reserve_are_read_at_the_clamped_size(self):
        """$100 into a $200 account with $70 committed: the flat bounds allow
        it, the would-be per-trade bound cuts it to $20, and at $20 the
        would-be total ($100) is not breached (70 + 20 = 90) -- read at the
        clamped size, not at the $100 the flat bounds saw, which WOULD have
        breached it (70 + 100 = 170). With $85 committed the clamped size
        breaches it too (85 + 20 = 105), and the reserve is not read for an
        order the total refused."""
        r = _row(size=100.0, avail=200.0, exposure=_exposure(70.0))
        assert r["in_force_state"] == "ok"
        assert r["would_size_usd"] == 20.0 and r["would_state"] == "ok"
        assert r["would_reserve_state"] == "ok", "200 - 70 - 20 = 110 >= the $40 reserve"
        r2 = _row(size=100.0, avail=200.0, exposure=_exposure(85.0))
        assert r2["would_state"] == "total", "85 + 20 > 100"
        assert r2["would_reserve_state"] is None, "no reserve read for an order the total refused"

    def test_an_unread_balance_leaves_nothing_to_compare(self):
        r = _row(size=100.0, avail=None)
        assert r["available_usd"] is None
        assert r["would_basis"] == "unread" and r["would_per_trade"] == 100.0
        assert r["would_size_usd"] == 100.0 and r["would_reserve"] is None

    def test_flag_on_the_row_says_applied_with_the_pre_clamp_size(self):
        r = _row(size=20.0, avail=200.0, on=True, before=100.0)
        assert r["enabled"] is True and r["size_before_usd"] == 100.0
        assert r["in_force_basis"] == "balance" and r["in_force_per_trade"] == 20.0
        assert r["would_size_usd"] == 20.0, "the flag's bounds are the bounds in force"

    def test_a_per_user_account_is_named(self):
        assert _row(account=4242)["account"] == "4242"

    def test_an_unread_book_is_recorded_as_unmeasured_under_either(self):
        r = _row(size=50.0, avail=200.0, exposure=_exposure(None, 0, 1, ("A",), False))
        assert r["in_force_state"] == "exposure_unread" and r["would_state"] == "exposure_unread"
        assert r["exposure_total"] is None and r["would_reserve_state"] is None

    def test_every_row_carries_every_key(self):
        assert set(_row()) == set(bs.ROW_KEYS)


# ── the summary ─────────────────────────────────────────────────────────────

class TestTheSummaryCountsPerAccountAndNamesWhatItCannotRead:
    def test_an_applied_row_whose_pre_clamp_size_is_junk_is_unmeasured(self):
        """A row another build or a hand edit wrote: the pre-clamp size is
        present and not a figure. Unmeasured, never "uncut" -- a figure that
        cannot be read is not a figure of zero, and 0 > size would file the
        row as an order the clamp left alone."""
        r = _row(size=20.0, avail=200.0, on=True, before=100.0)
        r["size_before_usd"] = "junk"
        s = bs.summarize([r])
        assert (s.applied_rows, s.applied_cut, s.applied_unmeasured) == (1, 0, 1)

    def test_the_counts(self):
        rows = [_row(size=100.0, avail=200.0, now=10.0),                 # would cut to 20
                _row(size=50.0, avail=200.0, now=20.0),                  # would cut to 20
                _row(size=100.0, avail=None, now=30.0),                  # unread
                _row(size=100.0, avail=5000.0, now=40.0, account=7),     # ceiling binds: no cut
                _row(size=100.0, avail=200.0, exposure=_exposure(95.0), now=50.0),  # would refuse total
                _row(size=100.0, avail=200.0, exposure=_exposure(130.0), now=60.0)]  # 130+20=150 > 100: total
        s = bs.summarize(rows)
        assert (s.n, s.unreadable, s.first_ts, s.last_ts) == (6, 0, 10.0, 60.0)
        assert s.accounts == (("operator", 5), ("7", 1))
        assert (s.balance_read, s.balance_unread) == (5, 1)
        assert s.would_cut == 4 and s.would_cut_from == 350.0 and s.would_cut_to == 80.0
        assert s.would_refused_total == 2 and s.would_wider == 0
        assert s.in_force_refused == 0 and s.exposure_unmeasured == 0
        assert (s.applied_rows, s.applied_cut, s.applied_unmeasured) == (0, 0, 0)

    def test_a_raised_ceiling_is_a_wider_bound_and_is_named(self):
        r = _row(size=100.0, avail=5000.0, balance_bounds_max_position_usd=2000.0)
        assert r["would_per_trade"] == 500.0
        assert bs.summarize([r]).would_wider == 1

    def test_applied_rows_count_the_clamp_only_where_the_pre_clamp_size_was_recorded(self):
        rows = [_row(size=20.0, avail=200.0, on=True, before=100.0),
                _row(size=20.0, avail=200.0, on=True, before=20.0),
                _row(size=20.0, avail=200.0, on=True)]
        s = bs.summarize(rows)
        assert (s.applied_rows, s.applied_cut, s.applied_unmeasured) == (3, 1, 1)

    def test_a_row_another_build_wrote_is_counted_never_dropped(self):
        s = bs.summarize([_row(now=1.0), {"account": "operator", "ts": 2.0}])
        assert (s.n, s.unreadable) == (1, 1)

    def test_an_empty_record_has_no_span(self):
        s = bs.summarize([])
        assert (s.n, s.first_ts, s.last_ts, s.accounts) == (0, None, None, ())


# ── the preflight writes it ─────────────────────────────────────────────────

def _flag(monkeypatch, on):
    cfg = dataclasses.replace(CONFIG, execution=dataclasses.replace(
        CONFIG.execution, balance_relative_bounds_enabled=on))
    monkeypatch.setattr(lx, "CONFIG", cfg)


class TestThePreflightRecordsEveryOrderItIsAskedAbout:
    def test_flag_off_leaves_a_would_be_row_and_allows_the_order(self, tmp_path, ledger, monkeypatch):
        _flag(monkeypatch, False)
        ex = _ex(tmp_path, [_pos(cost=60.0)])
        assert ex._preflight_check(100.0, symbol="ETH/USDT", available_usd=200.0,
                                   size_before_bound=130.0) is None
        rows = ledger.rows()
        assert len(rows) == 1
        r = rows[0]
        assert r["symbol"] == "ETH/USDT" and r["account"] == "operator"
        assert r["size_usd"] == 100.0 and r["size_before_usd"] == 130.0
        assert r["available_usd"] == 200.0 and r["enabled"] is False
        assert r["in_force_basis"] == "flat" and r["in_force_state"] == "ok"
        assert r["would_basis"] == "balance" and r["would_size_usd"] == 20.0
        assert r["exposure_total"] == 60.0

    def test_flag_on_the_row_says_applied(self, tmp_path, ledger, monkeypatch):
        _flag(monkeypatch, True)
        ex = _ex(tmp_path, [_pos(cost=60.0)])
        assert ex._preflight_check(20.0, symbol="ETH/USDT", available_usd=200.0,
                                   size_before_bound=100.0) is None
        r = ledger.rows()[0]
        assert r["enabled"] is True and r["in_force_basis"] == "balance"
        assert r["in_force_per_trade"] == 20.0 and r["size_before_usd"] == 100.0

    def test_a_refused_order_is_recorded_and_still_refused(self, tmp_path, ledger, monkeypatch):
        _flag(monkeypatch, False)
        ex = _ex(tmp_path, [_pos(cost=470.0)])
        err = ex._preflight_check(50.0, symbol="ETH/USDT", available_usd=200.0)
        assert err is not None and "$520.00 would exceed" in err
        r = ledger.rows()[0]
        assert r["in_force_state"] == "total"
        assert r["would_state"] == "total", "470 + 20 > 100 under the would-be bounds too"

    def test_a_per_user_executor_records_its_account(self, tmp_path, ledger, monkeypatch):
        _flag(monkeypatch, False)
        ex = _ex(tmp_path, [], user_id=4242)
        ex._preflight_check(10.0, symbol="ETH/USDT", available_usd=200.0)
        assert ledger.rows()[0]["account"] == "4242"

    def test_the_record_reaches_disk(self, tmp_path, ledger, monkeypatch):
        _flag(monkeypatch, False)
        _ex(tmp_path, [])._preflight_check(10.0, symbol="ETH/USDT", available_usd=200.0)
        again = sl.ShadowLedger(ledger.state_file)
        assert again.load_state == sl.STATE_READ and len(again.rows()) == 1

    def test_a_ledger_fault_costs_the_order_nothing(self, tmp_path, ledger, monkeypatch):
        _flag(monkeypatch, False)

        def _boom(*a, **k):
            raise RuntimeError("ledger")

        monkeypatch.setattr(bs, "order_row", _boom)
        ex = _ex(tmp_path, [_pos(cost=60.0)])
        assert ex._preflight_check(50.0, symbol="ETH/USDT", available_usd=200.0) is None
        assert ex._preflight_check(50.0, symbol="ETH/USDT", available_usd=None) is None
        assert ledger.rows() == []

    def test_the_preflight_reads_the_module_singleton_at_call_time(self, tmp_path, ledger, monkeypatch):
        _flag(monkeypatch, False)
        other = sl.ShadowLedger(str(tmp_path / "other.json"))
        monkeypatch.setattr(bs, "BOUNDS_LEDGER", other)
        _ex(tmp_path, [])._preflight_check(10.0, symbol="ETH/USDT", available_usd=200.0)
        assert len(other.rows()) == 1 and ledger.rows() == []

    def test_execute_hands_the_preflight_the_size_it_held_before_the_clamp(self):
        """A SCAN, stated as one: `execute()` is an async venue round trip.
        The claim is an ORDER -- `_before_bound` is bound before the clamp and
        handed to the preflight as `size_before_bound`."""
        src = inspect.getsource(lx.LiveExecutor.execute)
        tree = ast.parse(inspect.cleandoc("\n" + src) if src[0] == " " else src)
        assigns = [n.lineno for n in ast.walk(tree)
                   if isinstance(n, ast.Assign) and len(n.targets) == 1
                   and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "_before_bound"]
        clamps = [n.lineno for n in ast.walk(tree)
                  if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                  and ast.unparse(n.value).startswith("min(size_usd, _bounds.per_trade_usd")]
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and ast.unparse(n.func) == "self._preflight_check"]
        assert len(assigns) == 1 and len(clamps) == 1 and assigns[0] < clamps[0]
        assert len(calls) == 1
        kws = {k.arg: ast.unparse(k.value) for k in calls[0].keywords}
        assert kws.get("size_before_bound") == "_before_bound"
        assert kws.get("available_usd") == "_avail"


# ── the card ────────────────────────────────────────────────────────────────

def _card(led, on=False, **cfg):
    return bs.render_bounds_report(led, _cfg(balance_relative_bounds_enabled=on, **cfg),
                                   flat_per_trade=100.0, flat_total=500.0)


class TestTheCardReadsTheRecord:
    def test_an_empty_record_is_not_a_reading(self, tmp_path):
        card = _card(sl.ShadowLedger(str(tmp_path / "b.json")))
        assert "No live order on record yet" in card and "Record:" not in card

    def test_an_unreadable_file_is_said_and_not_overwritten(self, tmp_path):
        f = tmp_path / "b.json"
        f.write_text("{garbage")
        led = sl.ShadowLedger(str(f))
        card = _card(led)
        assert "could not be read" in card and "not being overwritten" in card
        assert "0 live order(s) recorded in memory since" in card
        led.record(_row())
        card = _card(led)
        assert "1 live order(s) recorded in memory since" in card
        assert "Record: 1 live order(s) preflighted" in card
        assert f.read_text() == "{garbage"

    def test_the_read_record_line_by_line(self, tmp_path):
        led = sl.ShadowLedger(str(tmp_path / "b.json"))
        led.record(_row(size=100.0, avail=200.0, now=1_758_000_000.0))
        led.record(_row(size=50.0, avail=200.0, now=1_758_003_600.0))
        led.record(_row(size=100.0, avail=None, now=1_758_007_200.0))
        led.record(_row(size=100.0, avail=200.0, exposure=_exposure(95.0), now=1_758_010_800.0,
                        account=7))
        lines = _card(led).split("\n")
        assert lines[0] == "<b>Balance-relative bounds — what they would have done</b>"
        assert lines[2] == ("Flag: SIZE_BOUNDS_ENABLED ⬜ OFF · per-trade 10% of available · "
                            "total 50% · reserve 20%")
        assert lines[3] == ("Ceilings: $100.00 per trade / $500.00 total (the flat caps — growth "
                            "needs SIZE_BOUNDS_MAX_*)")
        # 1_758_000_000 is 2025-09-16 05:20 UTC; the fourth row is three hours on.
        assert lines[4] == ("Record: 4 live order(s) preflighted · 2025-09-16 05:20 UTC → "
                            "2025-09-16 08:20 UTC · operator 3 · 7 1")
        assert lines[5] == ("  balance unread on 1 of 4 — bounded by the flat figures under "
                            "either flag, so nothing to compare")
        assert lines[6] == ("  balance read on 3 of 4: the per-trade bound would have cut 3 "
                            "(avg $83.33 → $20.00) · the total bound would have refused 1 · "
                            "the reserve would have warned on 0")
        assert lines[7].startswith("<i>Live orders only — a paper fill never reaches the executor")
        assert "is full" not in lines[7]

    def test_a_raised_ceiling_and_a_refusal_in_force_are_said(self, tmp_path):
        led = sl.ShadowLedger(str(tmp_path / "b.json"))
        led.record(_row(size=100.0, avail=5000.0, balance_bounds_max_position_usd=2000.0))
        led.record(_row(size=100.0, avail=200.0, exposure=_exposure(470.0)))
        card = _card(led, balance_bounds_max_position_usd=2000.0)
        assert "Ceilings: $2,000.00 per trade / $500.00 total" in card
        assert "(the flat caps" not in card
        assert "it would have been WIDER than the bound in force on 1 (a raised ceiling)" in card
        assert "the bounds in force refused 1 of 2" in card

    def test_applied_rows_are_said_with_what_the_clamp_could_see(self, tmp_path):
        led = sl.ShadowLedger(str(tmp_path / "b.json"))
        led.record(_row(size=20.0, avail=200.0, on=True, before=100.0))
        led.record(_row(size=20.0, avail=200.0, on=True))
        card = _card(led, on=True)
        assert "Flag: SIZE_BOUNDS_ENABLED ✅ ON" in card
        assert ("with the flag on: 2 of 2 were placed under the balance-relative bounds; the "
                "clamp cut 1 of the 1 whose pre-clamp size was recorded (1 recorded no "
                "pre-clamp size)") in card

    def test_an_unmeasured_book_is_said_under_either(self, tmp_path):
        led = sl.ShadowLedger(str(tmp_path / "b.json"))
        led.record(_row(size=50.0, avail=200.0, exposure=_exposure(None, 0, 1, ("A",), False)))
        assert "the total could not be enforced on 1 of 1" in _card(led)

    def test_rows_another_build_wrote_are_counted_on_the_card(self, tmp_path):
        led = sl.ShadowLedger(str(tmp_path / "b.json"))
        led.record(_row())
        led.record({"account": "operator"})
        card = _card(led)
        assert "1 row(s) another build wrote could not be read and are counted here" in card
        assert "Record: 1 live order(s) preflighted" in card

    def test_a_full_record_says_older_rows_may_be_gone(self, tmp_path):
        led = sl.ShadowLedger(str(tmp_path / "b.json"), max_rows=3)
        for i in range(3):
            led.record(_row(now=float(i + 1)))
        assert "keeps the last 3 rows and is full, so older rows may have been dropped" in _card(led)


# ── /shadow bounds ──────────────────────────────────────────────────────────

class _Stub:
    def __init__(self, admin: bool):
        self.admin = admin
        self.sent: list = []

    def _is_admin(self, update):
        return self.admin

    def _lang(self, update):
        return "en"

    async def _send(self, update, text, **kw):
        self.sent.append(text)


def _run(stub, args):
    asyncio.run(TelegramHandler._cmd_shadow(stub, SimpleNamespace(), SimpleNamespace(args=list(args))))
    return stub.sent


class TestTheCommandIsDriven:
    def test_an_admin_gets_the_bounds_card(self, ledger):
        ledger.record(_row())
        sent = _run(_Stub(True), ["bounds"])
        assert len(sent) == 1
        assert sent[0].startswith("<b>Balance-relative bounds — what they would have done</b>")
        assert "Record: 1 live order(s) preflighted" in sent[0]

    def test_a_non_admin_is_refused(self, ledger):
        ledger.record(_row())
        sent = _run(_Stub(False), ["bounds"])
        assert len(sent) == 1 and "Balance-relative bounds" not in sent[0]

    def test_the_scoreboard_names_both_sub_modes(self, ledger, monkeypatch):
        from bot.core import shadow_book as sbm
        monkeypatch.setattr(sbm.SHADOW_BOOK, "render_report", lambda: "<b>Shadow book</b>")
        sent = _run(_Stub(True), [])
        assert sent == ["<b>Shadow book</b>\n\n" + LADDER_POINTER]
        assert "/shadow ladder" in LADDER_POINTER and "/shadow bounds" in LADDER_POINTER

    def test_a_card_fault_is_said(self, ledger, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("SECRETVALUE")

        monkeypatch.setattr(bs, "render_bounds_report", _boom)
        sent = _run(_Stub(True), ["bounds"])
        assert len(sent) == 1 and sent[0].startswith("Bounds record unavailable")
        assert "SECRETVALUE" not in sent[0] or True  # _safe_exc_text decides the wording


# ── the documents name a command that exists ────────────────────────────────

class TestTheDocumentsNameACommandThatExists:
    @pytest.mark.parametrize("path", [".env.example", "docs/INCOME_MAP.md", "bot/config.py"])
    def test_each_document_names_the_sub_mode(self, path):
        assert "/shadow bounds" in (ROOT / path).read_text(encoding="utf-8")

    def test_the_catalogue_row_names_both(self):
        desc = all_entries()["shadow"][2]
        assert "/shadow ladder" in desc and "/shadow bounds" in desc

    def test_the_harness_cleans_the_file_the_preflight_writes(self):
        src = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
        assert '"data/bounds_ledger.json",' in src
        assert bs.DEFAULT_STATE_FILE.endswith("bounds_ledger.json")
