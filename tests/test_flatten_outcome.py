"""close_position answers by RETURN VALUE, and every reader of that answer reads it.

A venue-side failure inside `_close_position_inner` is caught, the position's
status is restored to open, and the caller receives `"CLOSE FAILED for …"`.
An unconfirmed or partial close comes back as a "kept OPEN" message whose own
text says whether a stop was re-placed on what it kept. Nothing raises. The file
says so beside `_reattempt_post_fill_sl` ("close_position signals failure by
RETURN VALUE … not by raising — a failed breach-close must not read as
closed") and used to honour it there alone.

Five readers on the live paths did not. The three post-fill guards each read
"the coroutine returned" as "the position is closed", and each had a handler
for a failed close that only a raise could reach — so on the one failure they
were written for, the overshoot guard rested the symbol, skipped the stop and
headed its card CLOSED over an open, over-levered position; the slippage
guard announced "CLOSED for safety"; and the fill-path guard told the operator
the stops had been cancelled and the position closed, when the cancel had
happened and the close had not. The entry path's own stop-placement flatten
and the post-fill ladder's flatten had the same shape. And the emergency
flatten rollup — the "Accounts flattened: N of N" line and the web
acknowledgement that stops the retry — matched only the strings a RAISE
produces, so a rejected close counted as flat on the most urgent screen in
the product.

`flatten_outcome` (bot/core/order_state.py, pure) is the one reading. It is
tested here as a function against the strings close_position actually
returns, pinned in BOTH directions — every marker is a string the method
returns, and every literal the method returns is classified here — and wired:
every reader consults it after the close and before it says anything.
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
from pathlib import Path

import pytest

from bot.core.live_executor import (
    LiveExecutor,
    execution_indicates_failure,
    stop_replacement_note,
)
from bot.core.order_state import (
    CLOSE_CARD_NOT_RENDERED,
    CLOSE_KEPT_OPEN_MARKERS,
    KEPT_OPEN_HEADINGS,
    close_card_is_wrong,
    close_did_not_happen,
    flatten_outcome,
)
from bot.utils.models import Direction
from tests.source_scan import code_only

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "bot" / "core" / "live_executor.py").read_text(encoding="utf-8")
CODE = code_only(SRC)


def _method(name: str) -> str:
    body = CODE[CODE.index(f"async def {name}("):]
    nxt = re.search(r"\n    (?:async )?def ", body[10:])
    return body[:nxt.start() + 10] if nxt else body


# ── the three answers ────────────────────────────────────────────────────────

@pytest.mark.parametrize("answer, expected", [
    ("CLOSE FAILED for t1: bitget {\"code\":\"40762\"}", "failed"),
    ("⚠️ CLOSE NOT CONFIRMED: LONG BTC/USDT\nThe position is kept OPEN and re-protected.",
     "kept_open"),
    ("⚠️ PARTIAL CLOSE — RESIDUAL REMAINS: LONG BTC/USDT\nPosition kept OPEN.", "kept_open"),
    ("Position t1 not found or already closed/closing.", "kept_open"),
    # the pending-limit cancel path's kept answers — unreachable from the guards
    # today (they all run on an open position), classified all the same
    ("Limit order for BTC/USDT filled while cancelling. Position is now open — use Close to exit.",
     "kept_open"),
    ("Failed to cancel limit order for BTC/USDT — order may still be active on exchange.",
     "kept_open"),
    ("Could NOT verify the cancel of BTC/USDT's limit order — the exchange did not answer.",
     "kept_open"),
    ("Limit order for BTC/USDT already filled. Position is now open — use Close to exit.",
     "kept_open"),
    ("BTC/USDT's limit order is gone, but the exchange did not tell us whether it FILLED first.",
     "kept_open"),
    ("CANCELLED pending LONG BTC/USDT limit order", "closed"),
    ("✅ CLOSED LONG BTC/USDT @ $100,000.00 (leverage_overshoot)", "closed"),
    ("CLOSED (unknown)", "closed"),
    ("Position BTC/USDT already booked closed — history read", "closed"),
])
def test_each_answer_reads_as_itself(answer, expected):
    assert flatten_outcome(answer) == expected


@pytest.mark.parametrize("unreadable", [None, "", 42, {"status": "closed"}])
def test_an_unreadable_answer_is_never_a_close(unreadable):
    """close_position is typed -> str. Anything else is a reading nobody made,
    and a guard that treated it as a close would stand down on nothing."""
    assert flatten_outcome(unreadable) == "failed"


def test_a_failed_close_is_not_softened_by_what_surrounds_it():
    """The failure marker wins even inside a longer message."""
    assert flatten_outcome("Attempted flatten.\nCLOSE FAILED for t1: timeout\nReview.") == "failed"


# ── the markers are close_position's own words, in both directions ───────────

def test_the_markers_are_the_strings_close_position_returns():
    """tuple → source. A marker that drifts from close_position's wording is a
    reader that silently files every kept-open answer under 'closed'."""
    inner = _method("_close_position_inner")
    assert 'return f"CLOSE FAILED for {trade_id}: {exc}"' in inner, (
        "the failure answer changed shape; flatten_outcome no longer recognises it")
    for marker in CLOSE_KEPT_OPEN_MARKERS:
        assert marker in inner, (
            f"{marker!r} is no longer a string _close_position_inner returns")


def _literal_text(node: ast.expr):
    """The constant text of a returned string expression, placeholders dropped;
    None for a name (`return close_msg`), whose text is classified elsewhere."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values
                       if isinstance(v, ast.Constant) and isinstance(v.value, str))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal_text(node.left), _literal_text(node.right)
        return (left or "") + (right or "") if (left or right) else None
    return None


# Every literal answer _close_position_inner can return, by a phrase of its
# own, and how it must read. A return that matches no row fails the test:
# a new answer is classified here before it can ship.
EXPECTED_ANSWERS = [
    ("not found or already closed", "kept_open"),
    ("filled while cancelling", "kept_open"),
    ("CANCELLED pending", "closed"),
    ("Failed to cancel limit order", "kept_open"),
    ("Could NOT verify the cancel", "kept_open"),
    ("already filled", "kept_open"),
    ("limit order is gone, but", "kept_open"),
    ("CLOSE NOT CONFIRMED", "kept_open"),
    ("RESIDUAL REMAINS", "kept_open"),
    ("CLOSE FAILED for", "failed"),
    # booked closed before the report after it raised: gone, and says so (the
    # "no card" phrase is CLOSE_CARD_NOT_RENDERED, interpolated, so the literal
    # text carries only the booking)
    ("— booked;", "closed"),
]


def test_every_literal_answer_of_the_close_is_classified():
    """source → table. The reverse of the pin above: a NEW kept-open wording
    added to close_position must be recognised, or the guards read it as a
    completed close. Walks every `return` of _close_position_inner."""
    src = textwrap.dedent(inspect.getsource(LiveExecutor._close_position_inner))
    returned = [t for node in ast.walk(ast.parse(src))
                if isinstance(node, ast.Return) and node.value is not None
                for t in [_literal_text(node.value)] if t]
    assert len(returned) >= 10, f"expected the close's literal answers, found {len(returned)}"
    for text in returned:
        rows = [(phrase, expected) for phrase, expected in EXPECTED_ANSWERS if phrase in text]
        assert rows, (
            f"_close_position_inner returns an answer this table does not know: "
            f"{text[:90]!r} — classify it in EXPECTED_ANSWERS (and, if it keeps the "
            f"position, add its marker to CLOSE_KEPT_OPEN_MARKERS)")
        for _phrase, expected in rows:
            assert flatten_outcome(text) == expected, (text[:90], expected)


# ── every flatten reader reads the verdict ───────────────────────────────────

READERS = [
    ("_leverage_overshoot_guard", "was CLOSED"),
    ("_post_fill_slippage_guard", "CLOSED for safety"),
    ("_guard_fill_leverage", "POSITION CLOSED"),
    ("_place_entry_stops", "CLOSED for safety"),
    ("_reattempt_post_fill_sl", "CLOSED for safety"),
]


@pytest.mark.parametrize("reader, closed_claim", READERS)
def test_the_reader_reads_the_answer_before_it_says_anything(reader, closed_claim):
    """Wiring. The behaviour of each arm is driven in the readers' own suites;
    this pins that the answer is READ, after the close and before any card, so
    the defect cannot come back as 'the coroutine returned'."""
    body = _method(reader)
    close = body.index("await self.close_position(")
    read = body.index("flatten_outcome(close_msg)")
    assert close < read, f"{reader} reads the verdict before it closes"
    assert 'if _outcome == "failed":' in body, f"{reader} has no failed arm"
    assert 'if _outcome == "kept_open":' in body, f"{reader} has no kept-open arm"
    # The card that says "closed" must sit after both arms have returned.
    claim = body.index(closed_claim)
    assert body.index('if _outcome == "kept_open":') < claim, (
        f"{reader} announces a close before it has ruled out a kept-open answer")


#: Every function anywhere under bot/ that awaits close_position and does NOT
#: read the answer itself, each with the reason that is allowed. A new caller
#: not listed here fails the pin below until it reads flatten_outcome or is
#: added with its reason — and an entry that no longer awaits close_position
#: fails the pin after it, so the list cannot carry dead names (it carried
#: three, for functions that go through close_all_positions and never awaited
#: close_position themselves).
HANDS_THE_ANSWER_ON = {
    # live_executor.py
    "close_all_positions": "appends each answer; the emergency rollup reads them",
    "check_positions": "returns its messages to the engine's monitor loop, which reads them",
    "_guard_unprotected_grace": "audits the verdict, then returns the answer to the ladder, which reads it",
    # callback_handler.py
    "_handle_callback": "hands the answer to _report_manual_close, which reads it",
    # engine_ops_commands.py
    "_cmd_liveclose": "prints the answer verbatim to the operator who asked",
}

_AWAITS_CLOSE = re.compile(r"await \w[\w.]*\.close_position\(")


def _functions(code: str):
    """(name, body) for every def in a module. A body ends at the next def OR
    class header at the same or a shallower indent — a class header counts,
    or a module-level function just before a class would swallow every method
    of that class. A head is a class head only when it STARTS with `class`:
    testing for the word anywhere in the match skipped every def whose name
    contains it (`classify`, `_classify_regime`, `_cmd_classpf` — seventeen
    functions), and an await inside one of those was acquitted silently."""
    pattern = re.compile(r"\n( *)(?:(?:async )?def|class) (\w+)[\(:]")
    heads = [(m.group(2), m.start(), len(m.group(1)),
              m.group(0).lstrip().startswith("class "))
             for m in pattern.finditer(code)]
    for i, (name, start, indent, is_class) in enumerate(heads):
        if is_class:
            continue
        end = len(code)
        for _n, nxt_start, nxt_indent, _c in heads[i + 1:]:
            if nxt_indent <= indent:
                end = nxt_start
                break
        yield name, code[start:end]


# Planted trees for the splitter, where the rule is the only thing in play: a
# real-tree assertion can pass for a reason unrelated to the rule (the class
# bug was caught only because one module happened to put a module-level def
# before a class), so the shapes are planted here.
_PLANTED = '''
def classify(x):
    return await ex.close_position(x)

async def _classify_regime(self):
    await self.ex.close_position("t")

def before_the_class():
    return 1

class CallbackHandler:
    async def _handle_callback(self):
        await executor.close_position("t")

    def helper(self):
        return 2
'''


def test_the_splitter_yields_a_def_whose_name_contains_class():
    bodies = dict(_functions(_PLANTED))
    assert "classify" in bodies and "_classify_regime" in bodies, sorted(bodies)
    assert _AWAITS_CLOSE.search(bodies["classify"])
    assert _AWAITS_CLOSE.search(bodies["_classify_regime"])
    assert "CallbackHandler" not in bodies, "a class head is not a function"


def test_the_splitter_does_not_let_a_module_level_def_swallow_a_class():
    bodies = dict(_functions(_PLANTED))
    assert "close_position" not in bodies["before_the_class"], (
        "the class head must end the module-level def's body")
    assert _AWAITS_CLOSE.search(bodies["_handle_callback"])
    assert "close_position" not in bodies["helper"]


def test_no_close_position_caller_under_bot_infers_a_close_from_a_return():
    """The other direction of the wiring pin, over the whole tree: every
    function under bot/ that awaits close_position either reads the answer
    (flatten_outcome) or hands it on to a reader — and says which, above.
    The version that scanned live_executor.py alone missed the NLP close
    handler and the smart exit, both live-money surfaces."""
    known_readers = {name for name, _ in READERS}
    unread = {}
    for path in sorted((ROOT / "bot").rglob("*.py")):
        if "backtest" in path.parts:
            continue                      # a different close_position (the backtest portfolio)
        code = code_only(path.read_text(encoding="utf-8"))
        for name, body in _functions(code):
            if not _AWAITS_CLOSE.search(body):
                continue
            if name in known_readers:
                assert "flatten_outcome(close_msg)" in body, (path.name, name)
                continue
            if "flatten_outcome(" in body:
                continue
            if name in HANDS_THE_ANSWER_ON:
                continue
            unread[f"{path.name}:{name}"] = True
    assert not unread, (
        f"close_position callers that neither read the answer nor are listed as "
        f"handing it on: {sorted(unread)}")


def test_every_listed_hand_off_still_awaits_close_position():
    """A stale allow-list entry is a caller that could be re-added unread.
    "Still exists" was the old check, and it cannot tell a stale entry from a
    hand-off: three entries named functions that never awaited close_position
    at all. The entry must name a function whose body awaits it."""
    awaiting = set()
    for path in (ROOT / "bot").rglob("*.py"):
        if "backtest" in path.parts:
            continue
        code = code_only(path.read_text(encoding="utf-8"))
        awaiting |= {name for name, body in _functions(code) if _AWAITS_CLOSE.search(body)}
    stale = [n for n in HANDS_THE_ANSWER_ON if n not in awaiting]
    assert not stale, f"listed hand-off callers that do not await close_position: {stale}"


def _handler_function(name: str) -> str:
    handler = code_only((ROOT / "bot" / "skills" / "callback_handler.py").read_text(encoding="utf-8"))
    return dict(_functions(handler))[name]


def test_the_two_readers_outside_the_executor_read_the_answer():
    """The NLP close handler used to know only 'CLOSE FAILED', so a kept-open
    answer rendered a 'closed' card with a $0.00 PnL nobody measured; the smart
    exit discarded the answer and notified 'closed' for every one. The handler
    reads it in `_report_manual_close` — the seam its behaviour is driven
    through in tests/test_manual_close_report.py — and hands the answer there."""
    call_site = _handler_function("_handle_callback")
    site = call_site.index('"manual_nlp"')
    assert "_report_manual_close(" in call_site[site:site + 900]
    reader = _handler_function("_report_manual_close")
    assert "flatten_outcome(result)" in reader
    assert '_outcome == "failed"' in reader and '_outcome == "kept_open"' in reader
    assert '_slot_id' in reader and '_this_id' in reader, (
        "the slot is matched on the trade id, not the symbol")
    assert "close_card_is_wrong(result)" in reader
    assert "pnl_usd or 0" not in reader, "no PnL is rebuilt from the record"
    engine = code_only((ROOT / "bot" / "core" / "engine.py").read_text(encoding="utf-8"))
    site = engine.index('reason=f"smart_exit:')
    window = engine[site - 400:site + 2400]
    assert "flatten_outcome(_answer)" in window
    assert 'result="CLOSE_FAILED"' in window and 'result="NOT_CLOSED"' in window
    assert 'result="CLOSING"' in engine[site - 1500:site], (
        "the audit written BEFORE the close must not say CLOSED")


# ── the readers beyond the executor ──────────────────────────────────────────

def test_the_emergency_flatten_rollup_reads_returned_failures():
    """close_all_positions appends close_position's answer verbatim and only a
    RAISE ever produced the 'Failed to close' shape, so a rejected close counted
    as flat: 'Accounts flattened: N of N' on the emergency card, and ok=True on
    the web acknowledgement that deletes the pending flatten and stops the
    retry."""
    from bot.formatters.drift_offer import flatten_account_ok, flatten_failed_messages

    closed = "✅ CLOSED LONG BTC/USDT @ $100,000.00 (emergency)"
    rejected = "CLOSE FAILED for T1: venue 5xx"
    kept = "⚠️ CLOSE NOT CONFIRMED: LONG ETH/USDT\nkept OPEN and re-protected."
    raised = "Failed to close ETH/USDT: 502"
    assert flatten_failed_messages([closed, rejected, kept, raised]) == [rejected, kept, raised]
    assert flatten_account_ok([closed]) is True
    assert flatten_account_ok(["No open positions to close."]) is True
    assert flatten_account_ok([closed, rejected]) is False
    assert flatten_account_ok([closed, kept]) is False


def test_kept_open_cards_do_not_carry_the_no_position_token():
    """The engine classifies execute()'s string: EXECUTION ABORTED means 'no
    position remains' and it stops tracking. A kept-open card describes a
    position that IS there. Driven per guard in their suites; pinned here as
    the rule."""
    assert execution_indicates_failure("⚠️ <b>EXECUTION ABORTED — BTC/USDT</b>\nCLOSED for safety")
    assert not execution_indicates_failure(
        "🚨 <b>BTC/USDT KEPT OPEN — flatten did not complete</b>\nReview it on the venue NOW.")
    for reader in ("_leverage_overshoot_guard", "_post_fill_slippage_guard", "_place_entry_stops"):
        body = _method(reader)
        kept_arm = body[body.index('if _outcome == "kept_open":'):]
        kept_arm = kept_arm[:kept_arm.index("return (") + 600]
        assert "KEPT OPEN" in kept_arm and "EXECUTION ABORTED" not in kept_arm, reader


def test_the_close_card_renderer_never_replaces_a_kept_open_text_with_a_picture():
    """alerts_monitor swaps the message for a rendered close card when a close
    slot exists — right for a close, wrong for a flatten that did not happen,
    where the text is the only warning. Its suppression used to be a
    hand-typed list of the guards' UPPER-CASE headings, which missed the
    close's own answers ("kept OPEN") that the monitor loop forwards raw; it
    reads order_state's vocabulary now."""
    monitor = code_only((ROOT / "bot" / "skills" / "alerts_monitor.py").read_text(encoding="utf-8"))
    decide = monitor.index("def close_card_for(")
    render = monitor.index("render_close_card(close_data)")
    assert decide < render, "the reading must precede the rendering"
    assert "close_card_for(" in monitor[monitor.index("async def _on_trade_closed"):render], (
        "the notifier must go through the seam, not re-decide inline")
    # …and the seam's behaviour is driven in tests/test_alerts_monitor_close_card.py,
    # because a window like this one passes against `if close_card_is_wrong(msg) and False:`.


def test_the_close_did_not_happen_reading_is_the_whole_vocabulary():
    """Every answer the close can give for a position it kept, and every
    heading the guards put over one, reads as 'did not happen'; a completed
    close does not."""
    for marker in CLOSE_KEPT_OPEN_MARKERS + KEPT_OPEN_HEADINGS:
        assert close_did_not_happen(f"… {marker} …"), marker
    # the close's own raw answers, as the monitor loop forwards them
    assert close_did_not_happen("⚠️ CLOSE NOT CONFIRMED: LONG BTC/USDT\nkept OPEN, NOT re-protected")
    assert close_did_not_happen("⚠️ PARTIAL CLOSE — RESIDUAL REMAINS: LONG BTC/USDT\nkept OPEN")
    assert not close_did_not_happen("✅ CLOSED LONG BTC/USDT @ $100,000.00 (TP)")
    assert not close_did_not_happen("⚠️ ENTRY ABORTED: BTC/USDT filled but the stop-loss could "
                                    "not be placed — position CLOSED for safety.")
    assert not close_did_not_happen(None)


def test_the_close_card_is_wrong_for_every_message_that_is_not_this_close():
    """The card renderers' reading: everything that did not happen, the
    safety abort whose text is the warning, and the booked close with no card
    behind it."""
    for marker in CLOSE_KEPT_OPEN_MARKERS + KEPT_OPEN_HEADINGS:
        assert close_card_is_wrong(f"… {marker} …"), marker
    assert close_card_is_wrong("⚠️ ENTRY ABORTED: BTC/USDT filled but … CLOSED for safety.")
    assert close_card_is_wrong(f"✅ CLOSED LONG BTC/USDT — booked; {CLOSE_CARD_NOT_RENDERED} (KeyError).")
    assert not close_card_is_wrong("✅ CLOSED LONG BTC/USDT @ $100,000.00 (TP)")
    assert not close_card_is_wrong("CLOSED LONG BTC/USDT (SL)\nEntry: $100.0000 → Exit: $95.0000")
    assert not close_card_is_wrong(None)


def test_the_re_place_promise_is_made_once_and_per_position():
    """Every card that says what the monitor will do about a missing stop
    goes through stop_replacement_note: the periodic re-place runs only for a
    position carrying both a stop level and a target, and the old sentence
    promised it unconditionally in six places."""
    assert "re-places one on its next pass" in stop_replacement_note(95.0, 110.0)
    # …and the reason names WHAT is missing, rather than assuming the target:
    # a first draft said "no target level" for a position whose target was the
    # leg it had.
    assert "no target level" in stop_replacement_note(95.0, 0.0)
    assert "no target level" in stop_replacement_note(95.0, None)
    assert "no stop level" in stop_replacement_note(0.0, 110.0)
    assert "no stop level and no target" in stop_replacement_note(0.0, 0.0)
    # The monitor's placement refuses BOTH clauses of the side check, and the
    # note reproduced only the positivity one: an inverted pair is positive on
    # both legs and refused on every attempt, forever.
    assert "wrong sides" in stop_replacement_note(110.0, 95.0, Direction.LONG)
    assert "wrong sides" in stop_replacement_note(95.0, 110.0, Direction.SHORT)
    assert "re-places one on its next pass" in stop_replacement_note(
        110.0, 95.0, Direction.SHORT)
    assert "re-places one on its next pass" in stop_replacement_note(
        95.0, 110.0, "LONG"), "a position's direction is a string"
    assert CODE.count("the monitor re-places") == 1, (
        "a card promises the re-place in its own words — route it through stop_replacement_note")
    assert "monitor re-places its stop" not in CODE
    for reader in ("_guard_fill_leverage", "_post_fill_slippage_guard",
                   "_leverage_overshoot_guard", "_place_entry_stops",
                   "_reattempt_post_fill_sl", "_close_position_inner"):
        assert "stop_replacement_note(" in _method(reader), reader


def test_the_monitor_loop_does_not_audit_a_kept_open_message_as_a_close():
    from bot.core.engine import RuneClawEngine

    kept = RuneClawEngine._is_kept_open_message
    assert kept("🚨 APT/USDT:USDT IS OVER-LEVERED — CLOSE DID NOT COMPLETE\n…")
    assert kept("🚨 BTC/USDT KEPT OPEN: the stop-loss could not be placed …")
    assert kept("CLOSE FAILED for T1: venue 5xx")
    assert kept("🚨 URGENT: BTC/USDT is LIVE with NO stop-loss and the safety close FAILED.")
    assert kept("🚨 <b>UNPROTECTED POSITION — BTC/USDT LONG</b>\nNo exchange stop-loss could be placed")
    # Derived from the one vocabulary: every kept-open answer the close can
    # give is read here without a second hand-typed list.
    for marker in CLOSE_KEPT_OPEN_MARKERS:
        assert kept(f"… {marker} …"), marker
    assert not kept("BTC/USDT LONG closed +$5.00 (TP)")
    assert not kept("⚠️ POSITION CLOSED — APT/USDT:USDT\nThe venue filled at 20x …")
    engine = code_only((ROOT / "bot" / "core" / "engine.py").read_text(encoding="utf-8"))
    consult = engine.index("self._is_kept_open_message(msg)")
    closed_audit = engine.index('action="live_auto_close", result="CLOSED"')
    assert consult < closed_audit, "the loop must consult the reading before it audits a close"


def test_no_card_claims_close_position_re_placed_a_stop():
    """close_position's final handler re-places nothing, and its re-protect
    path re-places only when it could. The guards' cards used to forward
    'close_position's own line below says what it kept and re-placed' —
    implying a first set of stops exists — and that line itself read
    're-protected' even when the re-placement had failed."""
    assert "says what it kept and re-placed" not in CODE
    assert "says what it re-placed" not in CODE
    assert "kept OPEN and re-protected rather than" not in CODE
    inner = _method("_close_position_inner")
    assert "NOT re-protected" in inner and 're-protected (stop {re_sl})' in inner, (
        "the re-protect answer must say what the re-placement did")
    assert inner.index("_protection = (") < inner.index("CLOSE NOT CONFIRMED: {pos.direction}")
