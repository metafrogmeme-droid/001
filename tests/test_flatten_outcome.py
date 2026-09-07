"""close_position answers by RETURN VALUE, and every reader of that answer reads it.

A venue-side failure inside `_close_position_inner` is caught, the position's
status is restored to open, and the caller receives `"CLOSE FAILED for …"`.
An unconfirmed or partial close comes back as a "kept OPEN" message with
whatever remains re-protected inside close_position. Nothing raises. The file
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

from bot.core.live_executor import LiveExecutor, execution_indicates_failure
from bot.core.order_state import CLOSE_KEPT_OPEN_MARKERS, flatten_outcome
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
    # booked closed before the report after it raised: gone, and says so
    ("booked; the close card could not be rendered", "closed"),
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
#: added with its reason.
HANDS_THE_ANSWER_ON = {
    # live_executor.py
    "close_all_positions": "appends each answer; the emergency rollup reads them",
    "check_positions": "returns its messages to the engine's monitor loop, which reads them",
    "_guard_unprotected_grace": "returns its answer to the ladder, which reads it",
    "_close_position_inner": "is the close",
    # engine.py
    "flatten_all_positions": "goes through close_all_positions and flatten_account_ok",
    "_maybe_flatten_web_requests": "goes through close_all_positions and flatten_failed_messages",
    # engine_ops_commands.py
    "_cmd_liveclose": "prints the answer verbatim to the operator who asked",
}


def _functions(code: str):
    """(name, body) for every def in a module. A body ends at the next def OR
    class header at the same or a shallower indent — a class header counts,
    or a module-level function just before a class would swallow every method
    of that class."""
    pattern = re.compile(r"\n( *)(?:(?:async )?def|class) (\w+)[\(:]")
    heads = [(m.group(2), m.start(), len(m.group(1)), "class" in m.group(0))
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
            if not re.search(r"await \w[\w.]*\.close_position\(", body):
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


def test_every_listed_hand_off_still_exists():
    """A stale allow-list entry is a caller that could be re-added unread."""
    names = set()
    for path in (ROOT / "bot").rglob("*.py"):
        code = code_only(path.read_text(encoding="utf-8"))
        names |= {name for name, _ in _functions(code)}
    missing = [n for n in HANDS_THE_ANSWER_ON if n not in names]
    assert not missing, f"listed hand-off callers that no longer exist: {missing}"


def test_the_two_readers_outside_the_executor_read_the_answer():
    """The NLP close handler used to know only 'CLOSE FAILED', so a kept-open
    answer rendered a 'closed' card with a $0.00 PnL nobody measured; the smart
    exit discarded the answer and notified 'closed' for every one."""
    handler = code_only((ROOT / "bot" / "skills" / "callback_handler.py").read_text(encoding="utf-8"))
    site = handler.index('"manual_nlp"')
    window = handler[site:site + 3000]      # code_only blanks comments in place, so the offsets stay
    assert "flatten_outcome(result)" in window
    assert '_outcome == "failed"' in window and '_outcome == "kept_open"' in window
    engine = code_only((ROOT / "bot" / "core" / "engine.py").read_text(encoding="utf-8"))
    site = engine.index('reason=f"smart_exit:')
    window = engine[site - 400:site + 1800]
    assert "flatten_outcome(_answer)" in window
    assert 'result="CLOSE_FAILED"' in window and 'result="NOT_CLOSED"' in window
    assert 'result="CLOSING"' in engine[site - 900:site], (
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
    where the text is the only warning. The suppression list must know the
    kept-open headings."""
    monitor = code_only((ROOT / "bot" / "skills" / "alerts_monitor.py").read_text(encoding="utf-8"))
    # Anchored to the tuple's own first entry (a string, which code_only keeps),
    # not to the comment beside it (which it blanks).
    anchor = monitor.index('"ENTRY ABORTED"')
    guard = monitor[anchor - 200:anchor + 200]
    assert "close_data = None" in guard, "the suppression no longer clears the close slot"
    for token in ("CLOSE FAILED", "URGENT", "KEPT OPEN", "DID NOT COMPLETE"):
        assert f'"{token}"' in guard, token


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
