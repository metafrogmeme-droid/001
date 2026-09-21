"""The quality ladder's shadow is READABLE: a record, a card, and the gate writes it.

Driven before this slice, `grep -rn quality_ladder bot/ scripts/` found the
SHADOW audit's writer (`risk_engine`, `action="quality_ladder"`,
`result="SHADOW"`), the two flags, the flag card's ON/OFF row -- and no
reader. The shadow reached a log line, which #36 had made VISIBLE, and nothing
made it READABLE: the operator who set the flags had nothing to read before
arming them. `bot/risk/ladder_shadow.py` is the record and `/shadow ladder`
the card.

WHAT IS DRIVEN HERE
-------------------
* the ledger's three load states, and a file that will not parse is NEVER
  overwritten -- rows recorded after it stay in memory and the card says so
* `evaluation_row`: measured / unmeasured, the would-be size only while the
  half is OFF, the rung's leverage exact and floor-aware, an applied row
  carrying no would-be
* `summarize`: per-rung counts, the unmeasured split, the span, and a row
  another build wrote COUNTED rather than dropped
* the gate writes ONE row per sized evaluation -- flags off (would-be),
  flags on (applied), a manual ticket (no rung) -- and a refusal before
  sizing leaves none; a ledger fault does not cost the trade its verdict or
  its margin-risk line
* the card, line by line, in each state; a measured ZERO on a rung is a
  reading; the FULL sentence only when the record is full
* `/shadow ladder` through the real handler: the card for an admin, the
  refusal for anyone else, and the scoreboard's pointer line naming the
  sub-mode -- a card that names a command claims the command does something
* every document that describes the shadow names the command, and the
  harness cleans the file the gate writes
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import pathlib
import tempfile
from datetime import datetime
from types import SimpleNamespace

import pytest

import bot.risk.risk_engine as rem
from bot.compat import UTC
from bot.config import CONFIG
from bot.risk import ladder_shadow as ls
from bot.risk import quality_ladder as ql
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.skills.command_catalog import all_entries
from bot.skills.telegram_handler import TelegramHandler
from bot.utils.models import Direction, RiskVerdict, TradeIdea

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── fixtures ────────────────────────────────────────────────────────────────

def _engine(balance=10_000.0):
    state = os.path.join(tempfile.mkdtemp(prefix="rc-ls-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=balance), state_file=state)


def _idea(conf=0.72, source="scan"):
    return TradeIdea(
        asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
        stop_loss=97.0, take_profit=109.0, confidence=conf,
        reasoning="quality", source=source, timestamp=datetime.now(UTC),
    )


def _cfg(size=False, lev=False, rungs=None):
    kw = dict(quality_ladder_size_enabled=size, quality_ladder_leverage_enabled=lev)
    if rungs is not None:
        kw["quality_ladder_rungs"] = rungs
    return dataclasses.replace(CONFIG, risk=dataclasses.replace(CONFIG.risk, **kw))


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    """A fresh ledger on a temp file, installed where the gate reads it."""
    led = ls.LadderLedger(str(tmp_path / "ledger.json"))
    monkeypatch.setattr(ls, "LADDER_LEDGER", led)
    return led


def _verdict(conf=0.72, source="scan", rungs=None):
    rungs = rungs if rungs is not None else ql.DEFAULT_RUNGS
    return ql.ladder_verdict(_idea(conf, source), rungs, "")


def _row(**kw):
    """A row as the gate writes it, with the defaults a rung-B shadow has."""
    base = ls.evaluation_row(idea=_idea(), verdict=_verdict(), size_usd=1300.0,
                             standard_leverage=5, floor=2, size_enabled=False,
                             leverage_enabled=False, now=1_758_000_000.0)
    base.update(kw)
    return base


# ── the ledger ──────────────────────────────────────────────────────────────

class TestTheLedgerHasThreeLoadStates:
    def test_no_file_is_fresh_and_the_first_row_creates_it(self, tmp_path):
        led = ls.LadderLedger(str(tmp_path / "l.json"))
        assert led.load_state == ls.STATE_FRESH and led.rows() == []
        assert led.record(_row()) is True
        assert json.loads((tmp_path / "l.json").read_text())["rows"][0]["rung"] == "B"

    def test_a_file_this_build_wrote_is_read_back(self, tmp_path):
        first = ls.LadderLedger(str(tmp_path / "l.json"))
        first.record(_row())
        again = ls.LadderLedger(str(tmp_path / "l.json"))
        assert again.load_state == ls.STATE_READ and len(again.rows()) == 1

    @pytest.mark.parametrize("body", ["{not json", '{"rows": "x"}', '{"rows": [1, 2]}', "[]"])
    def test_a_file_that_is_not_a_ledger_is_unreadable_and_never_overwritten(self, tmp_path, body):
        f = tmp_path / "l.json"
        f.write_text(body)
        led = ls.LadderLedger(str(f))
        assert led.load_state == ls.STATE_UNREADABLE and led.load_detail
        assert led.record(_row()) is False, "a row is kept in memory, and answers that it did not reach disk"
        assert len(led.rows()) == 1 and led.recorded_since_load == 1
        assert f.read_text() == body, "the file nobody could read is somebody's evidence"

    def test_the_newest_max_rows_are_kept(self, tmp_path):
        led = ls.LadderLedger(str(tmp_path / "l.json"))
        for i in range(ls.MAX_ROWS + 3):
            led.record(_row(ts=float(i)))
        rows = led.rows()
        assert len(rows) == ls.MAX_ROWS and led.full
        assert rows[0]["ts"] == 3.0 and rows[-1]["ts"] == float(ls.MAX_ROWS + 2)

    def test_a_write_fault_never_raises(self, tmp_path, monkeypatch):
        led = ls.LadderLedger(str(tmp_path / "l.json"))

        def _boom(*a, **k):
            raise OSError("disk")

        from bot.utils import shadow_ledger as sl
        monkeypatch.setattr(sl, "atomic_write_json", _boom)
        assert led.record(_row()) is False
        assert len(led.rows()) == 1


# ── the row ─────────────────────────────────────────────────────────────────

class TestTheRowSaysWhatTheRungWouldDo:
    def test_a_measured_rung_off_carries_the_would_be_figures(self):
        r = _row()
        assert (r["rung"], r["measured"], r["confidence"]) == ("B", True, 0.72)
        assert r["size_usd"] == 1300.0 and r["size_would_usd"] == 975.0
        assert (r["leverage_std"], r["leverage_ladder"]) == (5, 4)
        assert r["size_enabled"] is False and r["leverage_enabled"] is False

    def test_a_measured_rung_on_carries_the_applied_figure_and_no_would_be(self):
        r = ls.evaluation_row(idea=_idea(), verdict=_verdict(), size_usd=975.0,
                              standard_leverage=5, floor=2, size_enabled=True,
                              leverage_enabled=True)
        assert r["size_usd"] == 975.0 and r["size_would_usd"] is None
        assert r["leverage_ladder"] == 4, "the rung's leverage is recorded whether or not it applied"

    def test_the_top_rung_cuts_nothing(self):
        r = ls.evaluation_row(idea=_idea(0.92), verdict=_verdict(0.92), size_usd=1300.0,
                              standard_leverage=5, floor=2, size_enabled=False,
                              leverage_enabled=False)
        assert r["rung"] == "A" and r["size_would_usd"] is None and r["leverage_ladder"] is None

    def test_a_manual_ticket_is_unmeasured_with_no_rung(self):
        r = ls.evaluation_row(idea=_idea(1.0, "manual"), verdict=_verdict(1.0, "manual"),
                              size_usd=1300.0, standard_leverage=5, floor=2,
                              size_enabled=False, leverage_enabled=False)
        assert r["measured"] is False and r["rung"] is None and r["confidence"] is None
        assert r["size_would_usd"] is None and r["leverage_ladder"] is None
        assert r["source"] == ql.MANUAL_SOURCE

    def test_the_rung_leverage_is_floor_aware(self):
        r = ls.evaluation_row(idea=_idea(0.60), verdict=_verdict(0.60), size_usd=100.0,
                              standard_leverage=3, floor=2, size_enabled=False,
                              leverage_enabled=False)
        assert r["leverage_ladder"] == 2, "x0.60 of 3x is 1x, under the 2x floor"

    def test_every_row_carries_every_key(self):
        assert set(_row()) == set(ls.ROW_KEYS)


# ── the summary ─────────────────────────────────────────────────────────────

class TestTheSummaryCountsWhatItCanReadAndNamesWhatItCannot:
    def test_per_rung_counts_and_the_unmeasured_split(self):
        rows = [_row(ts=10.0),
                _row(ts=20.0),
                ls.evaluation_row(idea=_idea(0.92), verdict=_verdict(0.92), size_usd=1300.0,
                                  standard_leverage=5, floor=2, size_enabled=False,
                                  leverage_enabled=False, now=30.0),
                ls.evaluation_row(idea=_idea(1.0, "manual"), verdict=_verdict(1.0, "manual"),
                                  size_usd=1300.0, standard_leverage=5, floor=2,
                                  size_enabled=False, leverage_enabled=False, now=40.0),
                ls.evaluation_row(idea=SimpleNamespace(asset="X", source="scan", confidence=None),
                                  verdict=ql.ladder_verdict(SimpleNamespace(confidence=None, source="scan")),
                                  size_usd=1300.0, standard_leverage=5, floor=2,
                                  size_enabled=False, leverage_enabled=False, now=50.0)]
        s = ls.summarize(rows)
        assert (s.n, s.unreadable, s.first_ts, s.last_ts) == (5, 0, 10.0, 50.0)
        by = {st.rung: st for st in s.by_rung}
        assert by["B"].n == 2 and by["B"].size_would == 2 and by["B"].lev_would == 2
        assert by["B"].size_sum == 2600.0 and by["B"].size_would_sum == 1950.0
        assert by["B"].lev_pairs == ((5, 4, 2),)
        assert by["A"].n == 1 and by["A"].size_would == 0 and by["A"].lev_would == 0
        assert (s.unmeasured_manual, s.unmeasured_other) == (1, 1)

    def test_an_applied_row_counts_as_applied_not_would(self):
        r = ls.evaluation_row(idea=_idea(), verdict=_verdict(), size_usd=975.0,
                              standard_leverage=5, floor=2, size_enabled=True,
                              leverage_enabled=True, now=1.0)
        st = ls.summarize([r]).by_rung[0]
        assert (st.size_applied, st.size_would, st.lev_applied, st.lev_would) == (1, 0, 1, 0)

    def test_a_row_another_build_wrote_is_counted_never_dropped(self):
        s = ls.summarize([_row(ts=1.0), {"rung": "B", "ts": 2.0}])
        assert (s.n, s.unreadable) == (1, 1)

    def test_an_empty_record_has_no_span(self):
        s = ls.summarize([])
        assert (s.n, s.first_ts, s.last_ts, s.by_rung) == (0, None, None, ())


# ── the gate writes it ──────────────────────────────────────────────────────

class TestTheGateRecordsEverySizedEvaluation:
    def test_flags_off_a_rung_b_idea_leaves_one_would_be_row(self, ledger, monkeypatch):
        monkeypatch.setattr(rem, "CONFIG", _cfg())
        chk = _engine().evaluate(_idea(0.72), atr=2.0)
        assert chk.verdict == RiskVerdict.APPROVED
        rows = ledger.rows()
        assert len(rows) == 1
        r = rows[0]
        assert r["rung"] == "B" and r["size_usd"] == chk.position_size_usd
        assert r["size_would_usd"] == round(chk.position_size_usd * 0.75, 2)
        assert (r["leverage_std"], r["leverage_ladder"]) == (int(CONFIG.exchange.default_leverage),
                                                              int(int(CONFIG.exchange.default_leverage) * 0.8))
        assert r["size_enabled"] is False and r["leverage_enabled"] is False
        assert r["symbol"] == "BTC/USDT" and r["source"] == "scan"

    def test_flags_on_the_row_says_applied(self, ledger, monkeypatch):
        monkeypatch.setattr(rem, "CONFIG", _cfg(size=True, lev=True))
        chk = _engine().evaluate(_idea(0.72), atr=2.0)
        r = ledger.rows()[0]
        assert r["size_enabled"] is True and r["leverage_enabled"] is True
        assert r["size_usd"] == chk.position_size_usd and r["size_would_usd"] is None
        assert r["leverage_std"] == int(CONFIG.exchange.default_leverage), \
            "the standard the rung cut FROM, captured before the half ran"
        assert r["leverage_ladder"] == int(int(CONFIG.exchange.default_leverage) * 0.8)
        st = ls.summarize(ledger.rows()).by_rung[0]
        assert (st.size_applied, st.lev_applied, st.size_would, st.lev_would) == (1, 1, 0, 0)

    def test_a_manual_ticket_leaves_an_unmeasured_row(self, ledger, monkeypatch):
        monkeypatch.setattr(rem, "CONFIG", _cfg())
        _engine().evaluate(_idea(1.0, "manual"), atr=2.0)
        r = ledger.rows()[0]
        assert r["measured"] is False and r["rung"] is None and r["source"] == "manual"

    def test_a_refusal_before_sizing_leaves_no_row(self, ledger, monkeypatch):
        monkeypatch.setattr(rem, "CONFIG", _cfg())
        chk = _engine().evaluate(_idea(0.72), atr=2.0, live_mode=True, live_equity=None)
        assert chk.verdict == RiskVerdict.REJECTED and "LIVE_EQUITY: unreadable" in chk.checks_failed
        assert ledger.rows() == []

    def test_the_record_reaches_disk(self, ledger, monkeypatch):
        monkeypatch.setattr(rem, "CONFIG", _cfg())
        _engine().evaluate(_idea(0.72), atr=2.0)
        again = ls.LadderLedger(ledger.state_file)
        assert again.load_state == ls.STATE_READ and len(again.rows()) == 1

    def test_a_ledger_fault_costs_the_trade_nothing(self, ledger, monkeypatch):
        monkeypatch.setattr(rem, "CONFIG", _cfg())

        def _boom(*a, **k):
            raise RuntimeError("ledger")

        monkeypatch.setattr(ls, "evaluation_row", _boom)
        chk = _engine().evaluate(_idea(0.72), atr=2.0)
        assert chk.verdict == RiskVerdict.APPROVED
        assert any(ln.startswith("MARGIN_RISK:") and "OK" in ln for ln in chk.checks_passed), \
            "the margin-risk verdict after the write site still ran"
        assert ledger.rows() == []

    def test_the_gate_reads_the_module_singleton_at_call_time(self, ledger, monkeypatch):
        """A ledger bound at import would be the seam-as-default-argument
        defect: the fixture above patches the module attribute, and the row
        has to land on THAT ledger."""
        monkeypatch.setattr(rem, "CONFIG", _cfg())
        other = ls.LadderLedger(os.path.join(tempfile.mkdtemp(prefix="rc-ls2-"), "o.json"))
        monkeypatch.setattr(ls, "LADDER_LEDGER", other)
        _engine().evaluate(_idea(0.72), atr=2.0)
        assert len(other.rows()) == 1 and ledger.rows() == []


# ── the card ────────────────────────────────────────────────────────────────

def _card(led, size=False, lev=False, rungs=None):
    return ls.render_ladder_report(led, _cfg(size, lev, rungs).risk)


class TestTheCardReadsTheRecord:
    def test_an_empty_record_is_not_a_reading(self, tmp_path):
        card = _card(ls.LadderLedger(str(tmp_path / "l.json")))
        assert "No sized evaluation on record yet" in card
        assert "Record:" not in card and "would have been cut" not in card

    def test_an_unreadable_file_is_said_and_not_overwritten(self, tmp_path):
        f = tmp_path / "l.json"
        f.write_text("{garbage")
        led = ls.LadderLedger(str(f))
        card = _card(led)
        assert "could not be read" in card and "not being overwritten" in card
        assert "0 sized evaluation(s) recorded in memory since" in card
        led.record(_row())
        card = _card(led)
        assert "1 sized evaluation(s) recorded in memory since" in card
        assert "Record: 1 sized evaluation(s)" in card, "what is in memory is still rendered"
        assert f.read_text() == "{garbage"

    def test_the_read_record_line_by_line(self, tmp_path):
        led = ls.LadderLedger(str(tmp_path / "l.json"))
        led.record(_row(ts=1_758_000_000.0))
        led.record(_row(ts=1_758_003_600.0))
        led.record(ls.evaluation_row(idea=_idea(0.92), verdict=_verdict(0.92), size_usd=1300.0,
                                     standard_leverage=5, floor=2, size_enabled=False,
                                     leverage_enabled=False, now=1_758_007_200.0))
        led.record(ls.evaluation_row(idea=_idea(1.0, "manual"), verdict=_verdict(1.0, "manual"),
                                     size_usd=1300.0, standard_leverage=5, floor=2,
                                     size_enabled=False, leverage_enabled=False,
                                     now=1_758_010_800.0))
        lines = _card(led).split("\n")
        assert lines[0] == "<b>Quality ladder — what the rungs would have done</b>"
        assert lines[2] == "Flags: size ⬜ OFF · leverage ⬜ OFF"
        assert lines[3].startswith("Table: A ≥0.85 → size x1.00 / leverage x1.00 · B ≥0.70")
        # 1_758_000_000 is 2025-09-16 05:20 UTC; the fourth row is three hours on.
        assert lines[4] == ("Record: 4 sized evaluation(s) · 2025-09-16 05:20 UTC → "
                            "2025-09-16 08:20 UTC · every account this bot evaluates for")
        assert lines[5] == "  <b>A</b> ≥0.85: 1 — full size, standard leverage"
        assert lines[6] == ("  <b>B</b> ≥0.70: 2 — size would have been cut x0.75 on 2 "
                            "(avg $1,300.00 → $975.00) · leverage would have been cut 5x→4x on 2")
        assert lines[7] == "  <b>C</b> ≥0.00: 0", "a measured zero on a rung is a reading"
        assert lines[8] == "  no rung: 1 (manual tickets 1 — a stamp, not a measurement)"
        assert lines[9] == ("With both halves on, the size would have been cut on 2 of 4 "
                            "and the leverage on 2 of 4.")
        assert lines[10].startswith("<i>Would-be size = the post-cap figure × the rung multiplier.")
        assert "is full" not in lines[10], "the FULL sentence only when the record is full"

    def test_an_applied_row_reads_as_cut_not_would_have(self, tmp_path):
        led = ls.LadderLedger(str(tmp_path / "l.json"))
        led.record(ls.evaluation_row(idea=_idea(), verdict=_verdict(), size_usd=975.0,
                                     standard_leverage=5, floor=2, size_enabled=True,
                                     leverage_enabled=True, now=1.0))
        card = _card(led, size=True, lev=True)
        assert "Flags: size ✅ ON · leverage ✅ ON" in card
        assert "size cut x0.75 on 1 (applied)" in card and "leverage cut 5x→4x on 1 (applied)" in card
        assert "would have been cut" not in card
        assert "A half that was on cut the size on 1 of 1 and the leverage on 1 of 1." in card

    def test_a_full_record_says_older_rows_may_be_gone(self, tmp_path):
        led = ls.LadderLedger(str(tmp_path / "l.json"))
        for i in range(ls.MAX_ROWS):
            led.record(_row(ts=float(i + 1)))
        card = _card(led)
        assert f"keeps the last {ls.MAX_ROWS} rows and is full, so older rows may have been dropped" in card

    def test_a_refused_table_is_said(self, tmp_path):
        led = ls.LadderLedger(str(tmp_path / "l.json"))
        led.record(_row())
        card = _card(led, rungs="A:0.85:1.3:1.0,C:0.0:0.5:0.6")
        assert "the configured table was refused" in card and "defaults above are in use" in card

    def test_a_rung_the_table_no_longer_has_is_named(self, tmp_path):
        led = ls.LadderLedger(str(tmp_path / "l.json"))
        led.record(_row(rung="Z"))
        card = _card(led)
        assert "rungs no longer in the table (the table changed): Z 1" in card

    def test_rows_another_build_wrote_are_counted_on_the_card(self, tmp_path):
        led = ls.LadderLedger(str(tmp_path / "l.json"))
        led.record(_row())
        led.record({"rung": "B"})
        card = _card(led)
        assert "1 row(s) another build wrote could not be read and are counted here" in card
        assert "Record: 1 sized evaluation(s)" in card

    def test_nothing_cut_on_a_top_rung_record_is_said_as_a_reading(self, tmp_path):
        led = ls.LadderLedger(str(tmp_path / "l.json"))
        led.record(ls.evaluation_row(idea=_idea(0.92), verdict=_verdict(0.92), size_usd=1300.0,
                                     standard_leverage=5, floor=2, size_enabled=False,
                                     leverage_enabled=False, now=1.0))
        assert "No row would have been cut, and none was" in _card(led)


# ── /shadow ladder ──────────────────────────────────────────────────────────

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
    ctx = SimpleNamespace(args=list(args))
    asyncio.run(TelegramHandler._cmd_shadow(stub, SimpleNamespace(), ctx))
    return stub.sent


class TestTheCommandIsDriven:
    def test_an_admin_gets_the_ladder_card(self, ledger):
        ledger.record(_row())
        sent = _run(_Stub(True), ["ladder"])
        assert len(sent) == 1
        assert sent[0].startswith("<b>Quality ladder — what the rungs would have done</b>")
        assert "Record: 1 sized evaluation(s)" in sent[0]

    def test_the_sub_mode_is_case_insensitive(self, ledger):
        sent = _run(_Stub(True), ["Ladder"])
        assert sent[0].startswith("<b>Quality ladder")

    def test_a_non_admin_is_refused_the_ladder_card(self, ledger):
        ledger.record(_row())
        sent = _run(_Stub(False), ["ladder"])
        assert len(sent) == 1 and "Quality ladder" not in sent[0]

    def test_the_scoreboard_names_the_sub_mode(self, ledger, monkeypatch):
        from bot.core import shadow_book as sbm
        monkeypatch.setattr(sbm.SHADOW_BOOK, "render_report", lambda: "<b>Shadow book</b>")
        sent = _run(_Stub(True), [])
        assert sent == ["<b>Shadow book</b>\n\n" + __import__(
            "bot.skills.engine_ops_commands", fromlist=["LADDER_POINTER"]).LADDER_POINTER]
        assert "/shadow ladder" in sent[0]

    def test_a_card_fault_is_said_and_nothing_else_is_sent(self, ledger, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("SECRETVALUE")

        monkeypatch.setattr(ls, "render_ladder_report", _boom)
        sent = _run(_Stub(True), ["ladder"])
        assert len(sent) == 1 and sent[0].startswith("Ladder record unavailable")


# ── the documents that describe the shadow name the door ───────────────────

class TestTheDocumentsNameACommandThatExists:
    @pytest.mark.parametrize("path", [".env.example", "docs/INCOME_MAP.md", "bot/config.py"])
    def test_each_document_names_the_sub_mode(self, path):
        assert "/shadow ladder" in (ROOT / path).read_text(encoding="utf-8")

    def test_the_gate_inventory_names_it_on_both_flags(self):
        from bot.guardian.gate_inventory import GATES
        for key in ("quality_ladder_size_enabled", "quality_ladder_leverage_enabled"):
            assert "/shadow ladder" in GATES[key][2]
            assert "only audited as SHADOW" not in GATES[key][2]

    def test_the_catalogue_row_names_it(self):
        assert "/shadow ladder" in all_entries()["shadow"][2], "(group title, audience, description)"

    def test_the_harness_cleans_the_file_the_gate_writes(self):
        src = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
        assert '"data/ladder_ledger.json",' in src
        assert ls.DEFAULT_STATE_FILE.endswith("ladder_ledger.json")
