"""THREE ABORT CARDS ANNOUNCED A FLATTEN AND NAMED NO CAUSE.

When `_place_sl_tp` comes back empty, the executor flattens the position it
has just opened and tells the operator one of three things:

    ⚠️ EXECUTION ABORTED — ARB/USDT
    Position opened but the stop-loss could not be placed, so it was CLOSED
    for safety.

    🚨 URGENT — ARB/USDT is LIVE with NO stop-loss
    Automatic close also FAILED. Close this position MANUALLY ...

    🚨 ARB/USDT KEPT OPEN — stop-loss not placed, flatten did not complete

None of the three said WHY, although `_note_sltp_error` had recorded it
seconds earlier and five other surfaces already read it back. A venue refusing
the trigger price, a reduce-only leg under the minimum size, an API key with
no futures permission and a network blip are four different remedies, and the
card that announces the abort gave the operator one sentence for all of them.

AND THE ONE REFUSAL THE BOT WORKS OUT FOR ITSELF RECORDED NOTHING. Both
placers have exactly one early exit — the side-sanity check, which computes a
precise sentence (`_sltp_side_error`), audits it at ERROR, and returned
`(None, None)`. So the refusal whose cause is known exactly was the one no
downstream reader could see at all.

THE FACT HAD THREE RENDERINGS AND ONE OF THEM WAS UNESCAPED:

    /positions              venue said: <code>{why[:120]}</code>   escaped
    the unprotected card    Venue reason: <code>{why}</code>       NOT escaped
    the proactive monitor   Venue rejected the stop: <code>…[:160]</code>  escaped

— and all three attributed the reason to the VENUE, which is false for three
of the four things the store actually holds.

Everything here is DRIVEN: the cards are rendered and read. A scan cannot see
whether a card reaches the reading, which is the one thing being asked.
"""
from __future__ import annotations

import ast
import inspect
from functools import lru_cache
from pathlib import Path

import pytest

from bot.core.sltp_reason import REASON_MAX, refusal_line, refusal_suffix, venue_reason

ROOT = Path(__file__).resolve().parents[1]


class TestTheReadingIsThreeValuedAndSafe:
    def test_a_recorded_reason_comes_back_escaped(self):
        # The unprotected card interpolated this raw. A rejection carrying `<`
        # makes Telegram refuse the WHOLE message, and the send chokepoint's
        # fallback then strips every tag — so the card arrives without its
        # markup, or not at all, on the message that says a position is naked.
        out = venue_reason('40774: side mismatch <reduceOnly> for "BTC"')
        assert out is not None
        assert "<reduceOnly>" not in out
        assert "&lt;reduceOnly&gt;" in out
        assert "&quot;BTC&quot;" in out

    @pytest.mark.parametrize("nothing", [None, "", "   ", "\n"])
    def test_nothing_recorded_is_None_never_an_empty_sentence(self, nothing):
        assert venue_reason(nothing) is None

    def test_one_truncation_limit_where_there_were_three(self):
        out = venue_reason("x" * 1000)
        assert out is not None and len(out) == REASON_MAX

    def test_the_store_keeps_exactly_what_the_readers_show(self):
        # The store kept 180 and the readers showed 120 / everything / 160.
        # Raising the display bound above the storage one would have silently
        # capped it in the store instead — two answers to one question.
        from bot.core.live_executor import LiveExecutor
        src = inspect.getsource(LiveExecutor._note_sltp_error)
        assert "REASON_MAX" in src, src


class TestTheSentenceIsSourceNeutral:
    """`_note_sltp_error` holds the venue's words for SOME refusals only."""

    def test_the_line_never_attributes_the_reason_to_the_venue(self):
        # Driven over what the store really holds: `str(exc)` from a ccxt
        # create_order (a NetworkError is nobody saying anything), the v3
        # client's `{code}: {msg}` (the venue), "success code but no order id
        # returned" (the BOT's reading) and `exception: …` (ours). Three of
        # the four are not the venue speaking, and three surfaces said it was.
        for recorded in ("success code but no order id returned",
                         "exception: Connection reset by peer",
                         "40774: side mismatch",
                         "side-sanity: LONG needs stop below entry"):
            line = refusal_line(recorded)
            assert "venue said" not in line.lower(), line
            assert "venue rejected" not in line.lower(), line
            assert "refused" in line.lower(), line

    def test_every_recorded_reason_reaches_the_sentence(self):
        assert "40774" in refusal_line("40774: side mismatch")

    def test_an_unrecorded_reason_gets_its_own_words_not_silence(self):
        # A card that simply says nothing about the cause reads as an abort
        # that HAD none. `refusal_line` is never "".
        line = refusal_line(None)
        assert line.strip()
        assert "no reason was recorded" in line
        assert "<code>" not in line, "nothing to quote"

    def test_the_suffix_always_carries_a_newline_and_a_sentence(self):
        for r in (None, "", "40774: nope"):
            out = refusal_suffix(r)
            assert out.startswith("\n") and out.strip()


def _placer_early_exit_records(func_name: str) -> bool:
    """Does this placer's ONE early `return None, None` record a reason?

    A SCAN, and it says so: both exits sit inside a 400-line async method
    behind a live `ccxt.Exchange`, so driving them would mean standing up a
    venue double to read a dict the method writes on the way past. What is
    asserted is structural and exact — the statement immediately before that
    return is a `_note_sltp_error` call — which is the property, not a
    spelling.
    """
    from bot.core import live_executor
    tree = ast.parse(Path(live_executor.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef) or node.name != func_name:
            continue
        for block in ast.walk(node):
            body = getattr(block, "body", None)
            if not isinstance(body, list):
                continue
            for i, stmt in enumerate(body):
                if not (isinstance(stmt, ast.Return)
                        and isinstance(stmt.value, ast.Tuple)
                        and len(stmt.value.elts) == 2
                        and all(isinstance(e, ast.Constant) and e.value is None
                                for e in stmt.value.elts)):
                    continue
                if i == 0:
                    return False
                prev = ast.dump(body[i - 1])
                if "_note_sltp_error" not in prev:
                    return False
        return True
    raise AssertionError(f"{func_name} not found")


class TestTheSideSanityRefusalIsRecorded:
    @pytest.mark.parametrize("placer", ["_place_sl_tp", "_place_sl_tp_v3"])
    def test_the_only_early_exit_tells_the_store_why(self, placer):
        assert _placer_early_exit_records(placer), (
            f"{placer}'s side-sanity refusal returns (None, None) without "
            "recording — the one cause the bot computes itself, invisible to "
            "every reader of _last_sltp_reason")


#: The three cards this slice is about, each anchored on a sentence unique to
#: the STOP-PLACEMENT abort. The generic headline is not the anchor: there are
#: FOUR `EXECUTION ABORTED` returns in this method, and the other three abort
#: for causes they already name (`Fill slipped {_slip:.2%}`, `The venue filled
#: at {_lev_got}x`). The first draft of this guard anchored on the headline and
#: accused a slippage card that was telling the truth — "a boundary that is
#: whatever happens to be next is a boundary that manufactures accusations".
STOP_ABORT_CARDS = {
    "the flatten worked": "Position opened but the stop-loss could not be placed",
    "the flatten FAILED too": "is LIVE with NO stop-loss</b>",
    "the flatten did not complete": "KEPT OPEN \u2014 stop-loss not placed",
}


@lru_cache(maxsize=1)
def _executor_returns() -> tuple:
    """Every `return` statement in live_executor.py, as source.

    Parsed and split ONCE: `ast.get_source_segment` re-splits the whole
    12,000-line file per node, which made this walk quadratic and the suite
    take three minutes.
    """
    from bot.core import live_executor
    lines = Path(live_executor.__file__).read_text(encoding="utf-8").split("\n")
    tree = ast.parse("\n".join(lines))
    return tuple(
        "\n".join(lines[n.lineno - 1:(n.end_lineno or n.lineno)])
        for n in ast.walk(tree) if isinstance(n, ast.Return)
    )


def _returns_holding(anchor: str):
    """Every `return` statement whose own source carries `anchor`.

    Per-RETURN, so there is no window to get wrong and no neighbouring card to
    acquit this one. A card that loses its cause line fails here even if the
    card beside it keeps one.
    """
    return [seg for seg in _executor_returns() if anchor in seg]


class TestTheThreeCardsNameTheCause:
    @pytest.mark.parametrize("label,anchor", sorted(STOP_ABORT_CARDS.items()))
    def test_the_card_reaches_the_refusal_reading(self, label, anchor):
        segs = _returns_holding(anchor)
        assert len(segs) == 1, f"{label}: {len(segs)} returns carry {anchor!r}"
        assert "refusal_suffix(" in segs[0], (
            f"the {label} card announces an abort and names no cause")
        assert "_last_sltp_reason(" in segs[0], (
            f"the {label} card must read the RECORDED reason, not invent one")

    def test_the_sibling_aborts_already_name_their_own_causes(self):
        # Reachability before fixing: the other three EXECUTION ABORTED returns
        # in this method abort for slippage and for a leverage overshoot, and
        # each already says so. Adding a stop-refusal line to them would name a
        # cause that is not theirs.
        assert _returns_holding("Fill slipped")
        assert _returns_holding("The venue filled at")
        for seg in _returns_holding("Fill slipped"):
            assert "refusal_suffix(" not in seg

    def test_the_rendered_abort_carries_the_recorded_reason_escaped(self):
        # The sentence the branch builds, built the same way and read.
        line = refusal_suffix("40774: side mismatch <x>")
        card = ("\u26a0\ufe0f <b>EXECUTION ABORTED \u2014 ARB/USDT</b>\n"
                "Position opened but the stop-loss could not be placed, "
                "so it was CLOSED for safety." + line)
        assert "40774" in card
        assert "<x>" not in card and "&lt;x&gt;" in card
        assert card.count("\n") >= 2

    def test_an_abort_with_nothing_recorded_still_says_so(self):
        card = "\u26a0\ufe0f ABORTED" + refusal_suffix(None)
        assert "no reason was recorded" in card
        assert "order history" in card, "and where to go and look"


class TestTheOtherThreeReadersAskTheSeam:
    """One escape, one limit, one attribution — where there were three."""

    @pytest.mark.parametrize("path,forbidden", [
        # The unescaped one.
        ("bot/core/live_executor.py", "Venue reason: <code>{_why}</code>"),
        # The 120-character copy, and its wrong attribution.
        ("bot/skills/trading_commands.py", "venue said"),
        # The 160-character copy, and its wrong attribution.
        ("bot/core/proactive_monitor.py", "Venue rejected the stop"),
    ])
    def test_the_old_rendering_is_gone(self, path, forbidden):
        from tests.source_scan import code_only
        src = code_only((ROOT / path).read_text(encoding="utf-8"))
        assert forbidden not in src, f"{path} still renders it itself"

    def test_the_monitor_really_CALLS_the_seam(self, monkeypatch):
        """Patch the reading and read what the card says.

        Forbidding the old literals is not enough and the mutation round said
        so: a reader that rebuilds the escape and the truncation in DIFFERENT
        words passes every assertion about the old ones. A byte-identical copy
        agrees with every fixture — the only thing that tells one walk from
        two is patching the seam the reader imported and seeing the answer
        come out the other end.
        """
        import bot.core.proactive_monitor as pm
        monkeypatch.setattr(pm, "venue_reason", lambda r: "PLANTED-READING")
        from tests.test_unprotected_position_alert import _mon, _pos
        monkeypatch.setattr(type(pm.CONFIG), "is_live", lambda self: True)
        alerts = _mon([_pos()], sltp_reason="40808: whatever")._check_unprotected_positions()
        assert len(alerts) == 1
        assert "PLANTED-READING" in alerts[0].body, alerts[0].body
        assert "40808" not in alerts[0].body, "it rebuilt its own"

    def test_positions_really_CALLS_the_seam(self):
        """Same claim, asserted structurally: `/positions` is a 400-line async
        handler behind a Telegram update, so the call is what can be read.
        A rebuilt inline copy loses the CALL, which is what the round's second
        survivor did.
        """
        from bot.skills import trading_commands as tc
        src = Path(tc.__file__).read_text(encoding="utf-8")
        lines = src.split("\n")
        tree = ast.parse(src)
        # DERIVED, not named: the first draft asserted `_cmd_livepositions`
        # and the renderer is `_render_livepositions_cards`, so it accused
        # correct code. The owner is whichever function renders the line.
        owners = [n for n in ast.walk(tree)
                  if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
                  and any("SL bot-managed" in ln
                          for ln in lines[n.lineno - 1:(n.end_lineno or n.lineno)])]
        assert owners, "nothing renders the bot-managed stop line any more"
        owner = min(owners, key=lambda n: (n.end_lineno or 0) - n.lineno)
        calls = [n for n in ast.walk(owner)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "venue_reason"]
        assert calls, (
            f"{owner.name} renders the refusal without asking the seam")

    @pytest.mark.parametrize("path", [
        "bot/skills/trading_commands.py",
        "bot/core/proactive_monitor.py",
    ])
    def test_the_reader_imports_the_seam_at_module_level(self, path):
        # A function-local import here would resolve, and a MODULE-level one
        # is what makes the parse gate and the import ratchet able to see it —
        # the first draft of this wiring put `venue_reason` nowhere at all and
        # both modules still imported, because the name is only touched inside
        # a function body.
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        top = [n for n in tree.body if isinstance(n, ast.ImportFrom)]
        assert any(n.module == "bot.core.sltp_reason" for n in top), path
