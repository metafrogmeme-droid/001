r"""A confidence measured about ANOTHER trade is not a licence to skip the human.

#422 closed the auto-confirm door on a hand-typed ticket, whose `confidence`
is a STAMP: `build_manual_idea` writes 1.0 on every one and nothing measured
it. It recorded, in `auto_confirm_refusal`'s own docstring, that the door was
still open one source over -- and said the reading could not close it:

    "no reading of a confidence can close it: the defect there is the
     GEOMETRY, not the number."

Half right, and the wrong half. Driven, at the dataclass defaults:

    reanalyzed_idea(original@0.92, 111.0).confidence  ->  0.92
    AUTO_CONFIRM_THRESHOLD                            ->  0.85
    AUTO_CONFIRM_LIVE_ENABLED                         ->  True
    auto_confirm_refusal(the re-offer)                ->  None   (MAY execute)

The geometry really is different, and more sharply than "different": the
re-offer's levels are flat percentages of the new price, so its reward:risk is
``TARGET_PCT/STOP_PCT`` -- a CONSTANT. An analyst thesis at 15:1 and one at
0.1:1 both come out ~2:1. So the number copied onto it was measured about a
setup that is not this one, and what closes the door is about the confidence
after all: not its VALUE, its SUBJECT.

THE MODULE'S WHOLE PURPOSE WAS THE THING BEING DEFEATED. `drift_offer`'s
header opens "The re-analysed trade is a DIFFERENT trade. Offer it; never
execute it", and the line that registers it into `_pending_ideas` sits four
lines above a comment reading "OFFERED, not executed ... executing it spends
money on a thesis the user never saw, on the strength of a button they pressed
for a different one". The auto-confirm loop swept that dict.

WHY THE CHECK IS AT THE DOOR AND NOT IN `quality_reading`, which is the
obvious home and is wrong. Its consumers take an unmeasured quality as
ABSTAIN, not as be-careful: `ladder_verdict` answers 1.0 ("no rung, no
reduction") and `kelly_confidence_factor` answers 1.0 ("half-Kelly
unscaled"). Driven on a re-offer of a 0.30-confidence thesis, routing it
through the reading moves size x0.50 -> x1.00, leverage 3x -> 5x and Kelly
x0.30 -> x1.00. It would have doubled the weakest re-offers and raised their
leverage while closing this door -- a fix loosening something in the
flattering direction. `test_the_sizing_path_is_byte_identical` is that
measurement kept.

AND #422's OWN TEST PINNED THIS AS THE CONTRACT. `test_the_refusal_is_derived_
not_a_list` carried a row labelled "auto_reanalyze (the drift re-offer)"
asserting it "must still auto-confirm" -- built as `_idea(source=
"auto_reanalyze")`, an object `reanalyzed_idea` never returns. A fixture that
cannot produce the state it names, pinning a half-fix as a requirement, in the
commit that fixed the neighbour. It is relabelled for what it really measures:
the refusal is decided by the PROVENANCE FIELD, not by the source string, and
that row is the proof -- same source, two verdicts.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
from types import SimpleNamespace

import pytest

from bot.config import CONFIG
from bot.core.adaptive_threshold import auto_confirm_is_disabled
from bot.core.engine import RuneClawEngine
from bot.formatters.drift_offer import (
    STOP_PCT,
    TARGET_PCT,
    reanalyzed_idea,
    render_reanalyzed_offer,
)
from bot.risk.confidence_floor import clears_confidence_floor
from bot.risk.quality_ladder import (
    auto_confirm_refusal,
    kelly_confidence_factor,
    ladder_leverage,
    ladder_verdict,
    quality_reading,
)
from bot.utils.models import Direction, TradeIdea

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASELINE = pathlib.Path(__file__).with_name("confidence_provenance_baseline.txt")


def _idea(**kw) -> TradeIdea:
    base = dict(asset="ETH/USDT:USDT", direction=Direction.LONG,
                entry_price=100.0, stop_loss=97.0, take_profit=106.0,
                confidence=0.92, reasoning="an analyst thesis", source="unknown")
    base.update(kw)
    return TradeIdea(**base)


def _offer(conf: float = 0.92, price: float = 111.0) -> TradeIdea:
    made = reanalyzed_idea(_idea(confidence=conf), price)
    assert made is not None, "the fixture must produce a re-offer"
    return made


# ---------------------------------------------------------------------------
# The defect, and that it was reachable.
# ---------------------------------------------------------------------------

def test_the_defect_was_reachable_at_the_dataclass_defaults():
    """Every component, read off the live config rather than remembered."""
    threshold = CONFIG.auto_confirm_threshold
    offer = _offer()
    assert not auto_confirm_is_disabled(threshold), (
        f"the sentinel that switches auto-confirm off is 1.0; the default is "
        f"{threshold}, so the loop runs")
    assert CONFIG.auto_confirm_live_enabled is True, (
        "and the SUPPRESSED_LIVE branch cannot fire at the default")
    assert offer.confidence >= threshold, (
        f"the copied confidence {offer.confidence} clears {threshold} -- which "
        f"is the whole defect: the bar is cleared by a number about another "
        f"trade")


def test_the_re_offer_is_refused_and_the_reason_names_the_original():
    original = _idea(confidence=0.92)
    offer = reanalyzed_idea(original, 111.0)
    why = auto_confirm_refusal(offer)
    assert why is not None, (
        "the drift re-offer must NOT be auto-confirmed -- its own module says "
        "'Offer it; never execute it'")
    assert original.id in why, (
        f"and the reason names WHICH trade the confidence was measured about, "
        f"because 'unmeasured' would be false of it: {why!r}")


def test_the_refusal_is_the_field_and_not_the_source():
    """Same source, two verdicts. This is what a derived reading buys.

    An idea carrying `source="auto_reanalyze"` and nothing else PASSES; the
    builder's real product is refused. A denylist of source names could not
    tell these apart, and would refuse both.
    """
    synthetic = _idea(confidence=0.92, source="auto_reanalyze")
    assert synthetic.confidence_inherited_from is None, (
        "the synthetic one declares no provenance -- that is the point of it")
    assert auto_confirm_refusal(synthetic) is None, (
        "a source string alone is not the defect and must not be refused")
    assert auto_confirm_refusal(_offer()) is not None, (
        "the builder's product is, and the difference is the field")


def test_the_re_offers_reward_risk_is_a_constant():
    """The geometry claim, driven rather than asserted.

    Both levels are flat percentages of the new price, so the ratio is
    TARGET_PCT/STOP_PCT whatever the analyst found. This is the sentence the
    module docstring makes and it is worth pinning: if either constant moves,
    the reasoning above stops being true and a test should say so.
    """
    seen = set()
    for sl, tp in ((97.0, 106.0), (99.0, 115.0), (90.0, 101.0)):
        original = _idea(stop_loss=sl, take_profit=tp)
        seen.add((original.risk_reward_ratio,
                  reanalyzed_idea(original, 111.0).risk_reward_ratio))
    analyst = {a for a, _ in seen}
    reoffer = {r for _, r in seen}
    assert len(analyst) > 1, (
        "the fixture must span several analyst ratios or it measures nothing")
    assert reoffer == {round(TARGET_PCT / STOP_PCT, 2)}, (
        f"every analyst ratio {sorted(analyst)} collapses to the constant "
        f"{TARGET_PCT / STOP_PCT}; got {sorted(reoffer)}")


# ---------------------------------------------------------------------------
# What the fix deliberately does NOT change.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("conf", [0.92, 0.72, 0.60, 0.30])
def test_the_sizing_path_is_byte_identical(conf):
    """The measurement that chose where the check goes.

    Putting it in `quality_reading` reads as the tidier design and points the
    wrong way: an unmeasured quality makes the ladder ABSTAIN, so the weakest
    re-offers would have LOST their reduction. A re-offer takes the rung its
    original's confidence buys, exactly as before.
    """
    original = _idea(confidence=conf)
    offer = reanalyzed_idea(original, 111.0)
    ov, nv = ladder_verdict(original), ladder_verdict(offer)
    assert (nv.rung, nv.size_mult) == (ov.rung, ov.size_mult), (
        f"the re-offer must keep the original's rung: {ov.rung}/{ov.size_mult} "
        f"vs {nv.rung}/{nv.size_mult}")
    assert ladder_leverage(5, nv) == ladder_leverage(5, ov)
    assert kelly_confidence_factor(offer)[0] == kelly_confidence_factor(original)[0]
    assert quality_reading(offer).measured is True, (
        "the number IS a measurement -- of another trade. Calling it unmeasured "
        "is what would have loosened the sizing")


def test_the_human_door_is_untouched():
    """Proposing and executing are different acts; only the second narrows."""
    offer = _offer()
    assert clears_confidence_floor(offer), (
        "the re-offer must still be PROPOSABLE -- the card, and its Confirm "
        "button, are the whole point of the module")
    card = render_reanalyzed_offer(_idea(), offer)
    assert "not</b> the setup you confirmed" in card, (
        "and the card still says what it is")


@pytest.mark.parametrize("source", ["unknown", "scan_skill"])
def test_every_other_writer_still_auto_confirms(source):
    """The asymmetric arm. Without it the refusals above pass against a gate
    that refuses everything, which would stop the engine trading."""
    assert auto_confirm_refusal(_idea(confidence=0.92, source=source)) is None


# ---------------------------------------------------------------------------
# The tree walk: a SECOND producer must not be able to arrive silently.
# ---------------------------------------------------------------------------

def _carried_confidence(tree: ast.AST, rel: str) -> list[tuple[str, str, bool]]:
    """`(key, expr, declares)` per `TradeIdea(...)` carrying another
    object's `.confidence`.

    The key is `path::dotted.function#occurrence` and deliberately carries no
    line number -- see the baseline's own header.
    """
    scope: list[str] = []
    seen: dict[str, int] = {}
    out: list[tuple[str, str, bool]] = []

    class V(ast.NodeVisitor):
        def _named(self, node):
            scope.append(node.name)
            self.generic_visit(node)
            scope.pop()

        visit_FunctionDef = _named
        visit_AsyncFunctionDef = _named
        visit_ClassDef = _named

        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id == "TradeIdea":
                kw = {k.arg: k.value for k in node.keywords if k.arg}
                conf = kw.get("confidence")
                if isinstance(conf, ast.Attribute) and conf.attr == "confidence":
                    where = ".".join(scope) or "<module>"
                    n = seen.get(where, 0)
                    seen[where] = n + 1
                    out.append((f"{rel}::{where}#{n}",
                                ast.unparse(conf),
                                "confidence_inherited_from" in kw))
            self.generic_visit(node)

    V().visit(tree)
    return out


def _walk_tree(root: pathlib.Path) -> list[tuple[str, str, bool]]:
    found: list[tuple[str, str, bool]] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(("tests/", "node_modules/", ".venv/")) or "__pycache__" in rel:
            continue
        try:
            tree = ast.parse(path.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        found.extend(_carried_confidence(tree, rel))
    return found


def _baseline() -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in BASELINE.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, reason = line.partition("  ")
        rows[key.strip()] = reason.strip()
    return rows


def test_no_undeclared_carrier_of_another_confidence():
    undeclared = {k for k, _, declares in _walk_tree(ROOT) if not declares}
    unexplained = sorted(undeclared - set(_baseline()))
    assert not unexplained, (
        "these build a TradeIdea from another object's `.confidence` and "
        "declare no provenance, so `auto_confirm_refusal` cannot tell the "
        "number is about a different trade. Set "
        "`confidence_inherited_from=<origin id>`, or add a row to "
        f"tests/confidence_provenance_baseline.txt with the reason: {unexplained}")


def test_the_baseline_has_no_stale_row():
    """The other direction — an exemption kept past its reason."""
    undeclared = {k for k, _, declares in _walk_tree(ROOT) if not declares}
    stale = sorted(set(_baseline()) - undeclared)
    assert not stale, (
        f"these rows no longer describe anything -- the site declares its "
        f"provenance now, or is gone. Delete them in the same commit: {stale}")


def test_no_baseline_row_is_reasonless():
    bad = [k for k, reason in _baseline().items() if not reason]
    assert not bad, f"a row with no reason is an exemption nobody can check: {bad}"


def test_the_one_declaring_producer_is_the_drift_offer():
    declaring = sorted(k for k, _, d in _walk_tree(ROOT) if d)
    assert declaring == ["bot/formatters/drift_offer.py::reanalyzed_idea#0"], (
        f"the drift re-offer is the one producer that carries a confidence "
        f"across a geometry change and says so; got {declaring}")


# The rule itself is driven on a PLANTED tree, because on the real one every
# site is either declaring or baselined -- so a mutation of the RULE changes
# no verdict there, and a rule no input can reach is a claim that there is a
# check. (candle_hygiene_baseline.txt makes the same argument for its own.)

_PLANTED_ACCUSED = '''
def build(original):
    return TradeIdea(asset=original.asset, confidence=original.confidence)
'''

_PLANTED_CLEAN = '''
def build(original):
    return TradeIdea(asset=original.asset, confidence=original.confidence,
                     confidence_inherited_from=original.id)
'''

_PLANTED_OWN = '''
def build(req):
    return TradeIdea(asset="X", confidence=0.7)
'''

_PLANTED_NESTED = '''
class Maker:
    def build(self, original):
        def inner():
            return TradeIdea(asset="X", confidence=original.confidence)
        return inner()
'''

# A construction nested inside ANOTHER CALL's arguments. The nested-def
# fixture above descends through `visit_FunctionDef`, so it cannot tell
# whether `visit_Call` recurses -- deleting that recursion survived a green
# round until this existed.
_PLANTED_IN_ARG = '''
def build(original):
    return register(TradeIdea(asset="X", confidence=original.confidence))
'''


@pytest.mark.parametrize("src, expect_declares", [
    (_PLANTED_ACCUSED, [False]),
    (_PLANTED_CLEAN, [True]),
    (_PLANTED_OWN, []),
])
def test_the_rule_reads_a_planted_tree(src, expect_declares):
    found = _carried_confidence(ast.parse(src), "planted.py")
    assert [d for _, _, d in found] == expect_declares, found


def test_the_rule_descends_into_a_call_argument():
    found = _carried_confidence(ast.parse(_PLANTED_IN_ARG), "planted.py")
    assert [k for k, _, _ in found] == ["planted.py::build#0"], found


def test_the_rule_descends_into_a_nested_def():
    """A top-level-only walk acquits the inner one -- the `_web_aliases`
    lesson, which this repo has recorded twice."""
    found = _carried_confidence(ast.parse(_PLANTED_NESTED), "planted.py")
    assert [k for k, _, _ in found] == ["planted.py::Maker.build.inner#0"], found


# ---------------------------------------------------------------------------
# Driven end to end, through the real loop.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_re_offer_registered_mid_scan_is_not_auto_confirmed():
    """The same race #422 established, with the drift offer in the window.

    Written when `_tick` returned early while anything was pending and
    `_force_scan_locked` cleared the whole dict, so only an offer registered
    DURING the scan reached this loop -- which is exactly what the callback
    does: `_cmd_confirm`'s drift retry writes into `_pending_ideas` and takes
    no lock. Both loops now read the engine's own ideas only, so the offer is
    refused twice over (it is a person's, and its confidence is inherited);
    `test_a_persons_pending_idea_is_not_the_engines.py` drives the first.
    """
    confirmed: list[str] = []
    offer = _offer()
    engine = SimpleNamespace()

    async def _scan():
        return [SimpleNamespace(symbol="BTC/USDT:USDT")]

    async def _batched(signals, lightweight=False):
        # the race: the user taps confirm on a drifted card mid-scan
        engine._pending_ideas[offer.id] = offer
        return [_idea(asset="BTC/USDT:USDT", confidence=0.95)]

    async def _confirm(tid, user_id=""):
        confirmed.append(tid)
        return "ok"

    engine._pending_ideas = {}
    engine._pending_atr = {}
    engine._pending_timing = {}
    engine._pending_pyramid = {}
    engine._cooldown_until = 0.0
    engine._last_scan_signals = []
    engine.scanner = SimpleNamespace(scan=_scan)
    engine._transition = lambda *a, **k: None
    engine._analyze_signals_batched = _batched
    engine.confirm_trade = _confirm
    engine._auto_confirm_notify_callback = None
    engine.analyzer = None
    engine._engine_idea_ids = set()
    for name in ("_force_scan_locked", "_auto_confirm_gate_value",
                 "_auto_confirm_suppressed", "_engine_pending_ids",
                 "_register_engine_idea"):
        setattr(engine, name, getattr(RuneClawEngine, name).__get__(engine))

    summary = await engine._force_scan_locked()

    assert offer.id not in confirmed, (
        "the drift re-offer must NOT be auto-confirmed -- its own module says "
        "'Offer it; never execute it'")
    assert confirmed, (
        "and the engine's OWN idea still auto-confirms -- without this arm the "
        "assertion above passes against a loop that stopped working")
    assert summary["auto_confirmed"] == 1
    assert offer.id in engine._pending_ideas, (
        "the offer is left PENDING for its human, not dropped")


def test_the_call_site_comment_is_now_true():
    """"OFFERED, not executed" sat four lines below the registration into the
    dict the loop swept. It is a claim about the product, so it is checked."""
    src = inspect.getsource(
        __import__("bot.skills.callback_handler", fromlist=["x"]))
    assert "OFFERED, not executed" in src, (
        "the comment this slice makes true is gone -- if it was deliberately "
        "reworded, move this assertion with it")
    assert "self.engine._pending_ideas[new_idea.id] = new_idea" in src, (
        "and it still registers into the swept dict, which is why the refusal "
        "has to be at the door rather than at the registration")


# ---------------------------------------------------------------------------
# THE SECOND DRIFT SITE. `drift_offer`'s module docstring says the
# auto-execution "goes" and the re-analysis "stays". It was converted at ONE
# of the two sites that do this. `scan_skill.py` still rebuilt the idea from
# an inline second copy of the same flat 3%/6%, with a hard-coded confidence
# and the literal reasoning "Auto re-analyzed after price drift" -- the exact
# string that docstring quotes as the thing it removed -- and then called
# `confirm_trade` on it. No race: a live Telegram path, one tap.
#
# The guard that should have caught it, `test_the_drift_retry_offers_and_
# never_confirms`, forbids the literal `confirm_trade(retry_id` over
# `handler_sources()` -- driven, 16 files, and `scan_skill.py` is not one of
# them, while the literal sat verbatim at scan_skill.py:1802. Coverage of a
# CLASS is not coverage of the claim read off it.
#
# So the rule here is DERIVED from the thing every such site has in common:
# the drift message it branches on.
# ---------------------------------------------------------------------------

_DRIFT_MARKERS = ("price drifted", "re-analyze")


def _drift_retry_blocks() -> list[tuple[str, ast.If]]:
    """Every `if`-block that reacts to the engine's price-drift rejection.

    Bounded by the AST node rather than by a character count, because a block
    that is "everything within N characters" is a boundary that manufactures
    accusations -- this repo records that three times.
    """
    out: list[tuple[str, ast.If]] = []
    for path in sorted((ROOT / "bot").rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        try:
            tree = ast.parse(path.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            test = ast.unparse(node.test)
            if all(m in test for m in _DRIFT_MARKERS):
                out.append((rel, node))
    return out


def test_both_drift_retry_sites_are_found():
    """The rule must see the site the old guard could not."""
    files = sorted({rel for rel, _ in _drift_retry_blocks()})
    assert files == ["bot/skills/callback_handler.py", "bot/skills/scan_skill.py"], (
        f"a drift-retry site appeared or vanished; the rule below only "
        f"protects what it can see: {files}")


@pytest.mark.parametrize("rel", ["bot/skills/callback_handler.py",
                                 "bot/skills/scan_skill.py"])
def test_a_drift_retry_offers_and_never_confirms(rel):
    blocks = [n for r, n in _drift_retry_blocks() if r == rel]
    assert len(blocks) == 1, f"expected one drift block in {rel}, got {len(blocks)}"
    body = ast.unparse(blocks[0])
    assert "render_reanalyzed_offer(" in body, (
        f"{rel} rebuilds the idea on drift and must OFFER it")
    assert "reanalyzed_idea(" in body, (
        f"{rel} must build it through the one leaf, not a second inline copy "
        f"of the flat placeholder geometry")
    confirms = [ast.unparse(c) for c in ast.walk(blocks[0])
                if isinstance(c, ast.Call)
                and isinstance(c.func, ast.Attribute)
                and c.func.attr == "confirm_trade"]
    assert not confirms, (
        f"{rel} executes the rebuilt trade instead of offering it: {confirms}")
    # AND IT MUST STOP THERE. Dropping the `return` survived the round: the
    # card is still sent, and control then falls through to the classifier
    # that reads the ORIGINAL drift-rejection `result` -- so the caller gets
    # the offer card with a failure message printed under it. Nothing above
    # can see that, because every claim it makes is still true.
    renders = [n.lineno for n in ast.walk(blocks[0])
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "render_reanalyzed_offer"]
    returns = [n.lineno for n in ast.walk(blocks[0]) if isinstance(n, ast.Return)]
    assert renders and returns and max(returns) > max(renders), (
        f"{rel} shows the offer and then keeps going; the drift branch has to "
        f"end there or the rejection message prints under the card "
        f"(renders at {renders}, returns at {returns})")


def test_the_inline_second_copy_of_the_geometry_is_gone():
    """The constants have one home. 0.97/1.06 were 1-STOP_PCT and 1+TARGET_PCT
    written out, in the same function as the rebuild."""
    # code_only, because the prose explaining the fix has to be able to name
    # the percentages it removed -- "a comment that quotes the string it
    # forbids" is indistinguishable from the code doing it, and the first
    # draft of this assertion accused my own comment.
    from source_scan import code_only
    src = code_only((ROOT / "bot/skills/scan_skill.py").read_text())
    for spelling in ("0.97", "1.03", "1.06", "0.94"):
        assert spelling not in src, (
            f"{spelling!r} is a second spelling of drift_offer's constants; "
            f"import STOP_PCT/TARGET_PCT instead")
    assert "STOP_PCT" in src and "TARGET_PCT" in src


def test_the_drift_offer_docstring_is_true_of_both_sites():
    """It is a claim about the product, made in prose, so it is checked."""
    header = (ROOT / "bot/formatters/drift_offer.py").read_text().split('"""')[1]
    assert "Offer it; never execute it" in header
    assert "Auto re-analyzed after price drift" in header, (
        "the docstring quotes the literal it says was removed -- if that "
        "quote goes, this assertion should move with it")
    for path in ("bot/skills/scan_skill.py", "bot/skills/callback_handler.py"):
        assert "Auto re-analyzed after price drift" not in (ROOT / path).read_text(), (
            f"{path} still carries the reasoning string the docstring says "
            f"was removed")


def test_a_re_offer_of_a_manual_ticket_does_not_launder_the_stamp():
    """`reanalyzed_idea` copies the confidence and OVERWRITES the source.

    So a drift re-offer built from a MANUAL ticket carries `confidence=1.0`
    under `source="auto_reanalyze"` -- and #422's refusal, which keys on
    `source == "manual"`, does not fire for it. That is the stamp laundered
    through a rebuild, and it is closed here as a side effect rather than by
    a rule of its own: the provenance field is set whatever the original was.

    Reachability is narrow and stated rather than claimed: `_confirm_trade_
    inner`'s drift branch is doubly exempt for a manual ticket (`is_manual`
    and `order_type == "limit"`), so the ordinary single-confirm flow does
    not reach it. This is the backstop for the day either exemption moves.
    """
    from bot.risk.quality_ladder import quality_reading as _qr
    manual = _idea(confidence=1.0, source="manual")
    assert auto_confirm_refusal(manual) is not None, "the stamp is refused"

    offer = reanalyzed_idea(manual, 111.0)
    assert offer.source == "auto_reanalyze", (
        "the rebuild overwrites the source -- this is the laundering step")
    assert _qr(offer).measured is True, (
        "and #422's manual row cannot see it any more, which is why the "
        "refusal below has to come from the provenance field")
    assert offer.confidence_inherited_from == manual.id
    why = auto_confirm_refusal(offer)
    assert why is not None and manual.id in why, (
        f"a re-offer of a manual ticket must still be refused: {why!r}")
