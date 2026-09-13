"""Which skill a typed sentence actually REACHES, per surface.

THE CARD PROMISED A DOOR AND THIS MODULE IS THE DOOR'S ADDRESS.
`capability_answer` prints every reachable skill under "Ask me in your own
words for any of these:", and six of its twenty-six rows were reached by no
router rule and no chat tool — `optimize`, `run_strategy`, `walk_forward`,
`quant_analyze` on both surfaces, plus `deepscan` and `pro_scan` on the web.
Driven, the card's own phrase for `optimize` ("run a parameter optimisation
over the recorded history") lands on `trade_journal` on Telegram and on a bare
403 on the web. That is `/vault`'s hint shape inverted: there a card named a
COMMAND that did nothing, here a card names a CAPABILITY and claims asking for
it does something.

THE SCAN DISPATCH WAS WRITTEN THREE TIMES AND ANSWERED THREE WAYS.
`telegram_handler`'s `scan_modes` sent `scan_deep`/`scan_full` to `deepscan`
and the three timeframe modes to `pro_scan`. `user_gateway`'s `_INTENT_ALIASES`
sent ALL FIVE to `scan_market` — the shallow movers scan. And a test carried a
third copy under a comment saying it was "copied from `_chat_turn`", which it
was not: it agreed with neither, and the counts it computed survived only
because every wrongly-aliased target happened to also be a registered skill.
A second copy of a map is a second answer; this was a third.

The table lives here once and every reader asks it — and it now has ONE
column. It had two, recorded as the code behaved, with a note saying the
disagreement was a real defect filed separately. This is that fix: the same
typed words run the same skill with the same arguments behind the same
paywall, whichever surface they arrive on.
"""
from __future__ import annotations

from typing import Optional, TypedDict

#: Surfaces this module knows about, and there are FOUR chat doors, not two.
#:
#: The first version of this tuple named two and its own comment named a third,
#: which was the tell. `words_reach` narrowed only when `surface == "web"`, so
#: every other string — `"public"`, `"api"`, a typo, the empty string — fell
#: through to "the router's whole vocabulary plus every chat tool": 36 names
#: including `halt`, `close_position` and `emergency_stop`, on the function
#: whose entire job is deciding what the capability card may PROMISE. That is
#: fail-OPEN on a door list, and it answered MORE for an unrecognised surface
#: (36) than for the one surface it modelled best (telegram, 33), because the
#: unrecognised branch skipped the scan dispatch too and kept raw router intent
#: names that are not skills at all.
#:
#: `public` and `api` are measured, not assumed. `_public_chat_turn` classifies
#: the message, keeps only `needs_live_market_data`'s boolean and hands the
#: text to `_llm_chat(public=True)`; `_chat_tools_for` returns `[]` for
#: `public or not user_id`; `api_bridge`'s `/chat` runs no router at all. Zero
#: skills are reachable by words on either — the public scan gate is a REFUSAL,
#: which is not a door.
SURFACES = ("telegram", "web", "public", "api")

#: The surfaces on which typing words reaches no skill whatsoever.
_WORDLESS_SURFACES = frozenset({"public", "api"})


class UnknownSurface(ValueError):
    """Raised for a surface this module has not measured.

    GUARD, not omit — the CLAUDE.md table's first row. A door list has one
    consumer, the card, and a card built from a silently-empty or silently-full
    set is exactly the confident answer assembled from no reading that this
    repo spends its guard tests preventing. An unmeasured surface is neither
    "reaches everything" nor "reaches nothing"; it is a question nobody asked,
    so the caller is made to notice.
    """

class ScanRow(TypedDict):
    """One scan intent's dispatch: the skill, and the arguments it needs.

    Typed rather than `dict[str, object]` so `row["kwargs"]` is a mapping to
    every reader — the untyped version needed a `type: ignore` on the one line
    that copies it, which is a silenced question about the shape of the table
    this whole module exists to be the answer about.
    """

    skill: str
    kwargs: dict[str, str]


#: The five scan-mode intents: the skill each RUNS and the kwargs it needs.
#:
#: ONE COLUMN. It had two — telegram and web — and they disagreed: the same
#: typed words ran the full universe sweep on Telegram and the shallow scan
#: on the web, behind a different paywall, decided only by which surface the
#: caller happened to use. That is collapsed, so `surface` is no longer a
#: parameter of this question at all; `words_reach` still narrows by surface,
#: because whether a skill is REACHABLE there is a different question from
#: which skill an intent runs.
#:
#: THE KWARGS ARE PART OF THE DISPATCH, and leaving them out is a silent
#: defect rather than a loud one. Driven, `intent.kwargs` is `{}` for all five
#: intents, and all three scan skills are `execute(self, engine, **kwargs)` —
#: so a missing `mode` raises NOTHING. `ProScanSkill` does
#: `MODE_CFG.get(mode, MODE_CFG["intraday"])`, so a scalp request would have
#: rendered "RUNECLAW INTRADAY SCAN / Timeframe: 15M" with no marker of any
#: kind. A dispatch table that names the skill and not its arguments is half a
#: table.
#:
#: RECORDED, NOT FIXED: `scan_full`'s rule explains itself as "Full scan with
#: patterns" and its waiting message says the same, and it runs the identical
#: 4h sweep `scan_deep` does — `resolve_timeframes("all")` is what would make
#: the word true, and it multiplies the sweep by the number of timeframes.
#: That is a decision about what a paid scan costs, not a transcription, so
#: this table reproduces what Telegram has always dispatched rather than
#: quietly changing it. Written down is how it stops being invisible.
SCAN_DISPATCH: dict[str, ScanRow] = {
    "scan_deep": {"skill": "deepscan", "kwargs": {"timeframe": "4h"}},
    "scan_full": {"skill": "deepscan", "kwargs": {"timeframe": "4h"}},
    "scan_swing": {"skill": "pro_scan", "kwargs": {"mode": "swing"}},
    "scan_scalp": {"skill": "pro_scan", "kwargs": {"mode": "scalp"}},
    "scan_intraday": {"skill": "pro_scan", "kwargs": {"mode": "intraday"}},
}


def dispatches_to(intent: str) -> str:
    """The skill `intent` actually runs, on every surface.

    An intent this table does not name dispatches to ITSELF — that is the
    ordinary case and the table is only for the intents whose name is not the
    skill's. Returning the intent unchanged rather than None keeps every caller
    from having to spell the same fallback.

    `surface` was a parameter here and is not one any more: it existed only to
    pick between two columns that should never have differed.
    """
    row = SCAN_DISPATCH.get(intent)
    if not row:
        return intent
    return row["skill"]


def dispatch_kwargs(intent: str) -> dict[str, str]:
    """The arguments `intent` must be dispatched WITH, or `{}`.

    A fresh dict each call: these reach `skill.execute(**kwargs)` beside a
    caller-supplied `user_id`, and a shared literal would let one turn's
    mutation reach the next.
    """
    row = SCAN_DISPATCH.get(intent)
    if not row:
        return {}
    return dict(row["kwargs"])


def _require_surface(surface: str) -> None:
    if surface not in SURFACES:
        raise UnknownSurface(
            f"{surface!r} is not a surface this module has measured; "
            f"known: {', '.join(SURFACES)}")


def web_scan_aliases() -> dict[str, str]:
    """The web's alias map, derived from the one table.

    `user_gateway` built this inline and a test copied it wrongly. It is a
    derivation now, so a change to the table reaches both without anybody
    remembering to edit a second place. Since the columns collapsed this is
    the SAME map Telegram dispatches — which is the whole point, and the
    reason the name keeps "web" in it is that `user_gateway` is still its one
    reader.
    """
    return {k: dispatches_to(k) for k in SCAN_DISPATCH}


def words_reach(surface: str, *,
                tools: Optional[set[str]] = None) -> set[str]:
    """Skill names a caller can reach on `surface` BY TYPING WORDS.

    Three sources, because there are three doors and a card that promises one
    has to know about all of them:

    * a router rule that emits the name (mapped through `SCAN_DISPATCH`,
      because the router's name for a sentence is not always the skill that
      runs);
    * a chat TOOL the model may call — ``tools`` is the catalogue THIS caller
      actually gets, and passing it is not optional for a per-caller card;
    * on the web, the skill must additionally be in `WEB_CHAT_SKILLS` — a
      routed intent the web refuses is not a door, it is a 403 one turn after
      the card invited the ask.

    A surface in `_WORDLESS_SURFACES` answers the empty set; one this module
    has not measured raises `UnknownSurface`. There is no third behaviour, and
    in particular there is no "everything" default — see `SURFACES`.

    ``tools=None`` means "no caller" — the question is what the PRODUCT does,
    not what this person reaches — and it answers from the static `CHAT_TOOLS`
    tuple. That is the right reading for `toolless_capability_answer`, which
    describes the product to somebody with no account, and the WRONG one for a
    signed-in card. It was the only reading available, and it was a second copy
    of a gate: the model's real catalogue is `_chat_tools_for`, which applies
    two filters this tuple knows nothing about — `CONFIG.llm.chat_tools_enabled`
    and `registry.get(name) is not None` — plus a bare `except: return []`.
    Driven with chat tools switched OFF, the model held ZERO tools, no router
    rule names `proposals`, `rejected_trades`, `check_event_risk` or
    `macro_brief`, and the card still printed all four under "Ask me in your
    own words for any of these". A card naming a capability whose only door is
    shut is this module's own stated subject.

    Imports are local: `chat_tools` imports this module's callers, and a
    module-level import here closes the cycle.
    """
    from bot.nlp.intent_router import routed_skill_names

    _require_surface(surface)
    if surface in _WORDLESS_SURFACES:
        # Measured, not assumed — see SURFACES. Returning the empty set here
        # is a reading; returning it for an unknown surface would be a guess,
        # which is why that case raises instead.
        return set()
    out: set[str] = {dispatches_to(name) for name in routed_skill_names()}
    out.discard("")
    if tools is None:
        from bot.nlp.chat_tools import CHAT_TOOLS
        out |= {t.name for t in CHAT_TOOLS}
    else:
        out |= set(tools)
    if surface == "web":
        from bot.skills.skill_permissions import WEB_CHAT_SKILLS
        out &= set(WEB_CHAT_SKILLS)
    return out


def doorless(names, surface: str, *,
             tools: Optional[set[str]] = None) -> list[str]:
    """Of `names`, the ones no words reach on `surface`, in the given order.

    The card's own filter. NOT a hand-written list of exceptions: a skill that
    grows a router rule tomorrow leaves this set without anybody editing it,
    and one that loses its rule joins it the same way.
    """
    reach = words_reach(surface, tools=tools)
    return [n for n in names if n not in reach]


def command_for(skill: str) -> Optional[str]:
    """The slash command that reaches `skill` when words do not, or None.

    Only for skills the catalogue genuinely names — a card that prints a
    command is claiming the command does something, so this answers None
    rather than guessing a plausible name. `/vault`'s hint shape is the whole
    reason this is a lookup against a table somebody wrote on purpose.
    """
    return _COMMAND_DOOR.get(skill)


#: Skill -> the command that reaches it. Every entry was checked against
#: `command_catalog.all_entries()` by the guard, so a rename breaks the test
#: rather than printing a command that does not exist.
_COMMAND_DOOR: dict[str, str] = {
    "optimize": "/optimize",
    "run_strategy": "/strategy",
    "walk_forward": "/walkforward",
    "quant_analyze": "/quant",
    "deepscan": "/deepscan",
    "pro_scan": "/scalp",
}
