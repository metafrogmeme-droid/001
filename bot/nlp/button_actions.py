"""Which button a tap came from, and nothing else that was on the payload.

A tapped button is a turn the user can SEE and the model cannot. The slash
slice closed that hole for 147 commands and the registration for this one sits
directly below it:

    handler = self._remembering(cmd, handler)          # every slash command
    app.add_handler(CommandHandler(cmd, handler))
    app.add_handler(CallbackQueryHandler(self._handle_callback))   # nothing

`_REPLY_CAPTURE`'s own comment records it — *"the free-text path, alerts and
callbacks are untouched"* — so it was filed rather than missed, and what it
left uncovered includes ``confirm:``, the button that EXECUTES A TRADE. The
free-text limit-price path records that same execution under a comment saying
*"A turn that PLACES A TRADE recorded nothing at all, so 'did that go
through?' reached the model with the confirmation missing from its own
history"*: fixed for the typed door, left standing on the tapped one.

**THE ACTION IS THE WHOLE RECORD. THE PAYLOAD IS NEVER RECORDED.**
``command_turn_text`` withholds a slash command's arguments because five
commands take a secret as theirs and the store is a file on disk. The same
rule is sharper here and for a second reason: ``confirm:T-1758059112:4242``
carries an internal trade id, and ``admit:<uid>`` carries SOMEBODY ELSE'S
Telegram id — recording it would put one user's identifier into another
user's prompt. So the payload is dropped at the boundary, never per branch.

**The table is derived and guarded rather than hand-written.** A map kept in
step by hand is the ``/setllm`` ten-of-eleven shape: the branch added tomorrow
is the one missing from it. ``tests/test_a_button_tap_is_in_the_transcript.py``
walks ``_handle_callback`` by AST for every literal ``data`` is compared
against and fails when the two sets differ — the ``guarded_commands_baseline``
rule, which is the only reading that cannot silently fall behind.

And the failure direction is chosen: an action this build does not name is
recorded as UNNAMED, never as its raw data. A table one row short then costs
the model information; the alternative costs a user their identifier.
"""

from __future__ import annotations

from typing import Optional

#: Every literal the callback dispatcher compares `data` against, whole
#: (`data == "closeall_confirm"`) or as a prefix (`data.startswith("confirm:")`).
#: Pinned against an AST walk of the dispatcher itself, so a branch added
#: without a row here fails rather than recording an unnamed tap.
BUTTON_ACTIONS: tuple[str, ...] = (
    "admit:",
    "closeall_cancel",
    "closeall_confirm",
    "confirm:",
    "duel:",
    "emergency_cancel",
    "emergency_confirm",
    "lang:",
    "latest_signal",
    "mode_",
    "nav:",
    "open_warroom",
    "orders",
    "pane:",
    "performance",
    "policy_",
    "policy_apply_enforce",
    "policy_cancel",
    "pos_close_",
    "pos_details_",
    "positions",
    "reject:",
    "risk_control",
    "risk_emergency_stop",
    "risk_pause",
    "risk_safe_mode",
    "scan_confirm:",
    "scan_limit:",
    "scan_reject:",
    "setlimit:",
    "signal_watch_",
    "stance_keep",
    "strategy_mode",
    "yld:",
    "yldf:",
)

#: What the record says when the tap matched no row. It is a real state, not a
#: fallback dressed as one: the dispatcher answers a branch this build does not
#: name, and the honest turn says so rather than quoting the payload.
UNNAMED = "unnamed"


def button_action(data: object) -> Optional[str]:
    """The dispatcher's own matched prefix for `data`, or None.

    LONGEST match, because the dispatcher tests some literals whole and some
    as prefixes and two rows can share an opening: ``policy_cancel`` is a
    branch of its own inside ``policy_``, and reading it as the shorter one
    would file a cancellation as a policy change.
    """
    # `isinstance` is the whole guard: no row in the table is empty, so an
    # empty string matches nothing by itself. A second `not data` check was
    # here and the mutation round could not kill it — an equivalent mutant is
    # the code claiming a check it does not make.
    if not isinstance(data, str):
        return None
    hit: Optional[str] = None
    for row in BUTTON_ACTIONS:
        if data == row or data.startswith(row):
            if hit is None or len(row) > len(hit):
                hit = row
    return hit


def action_label(data: object) -> str:
    """The action as a record names it — the matched prefix, trailing
    separator dropped, or `UNNAMED`.

    The separator is machinery: a reader of the transcript learns nothing
    from the colon in ``confirm:`` and a model reading ``mode_`` would be
    reading half a token. What is NOT dropped is the rest of the payload,
    which is the whole point of this module.
    """
    hit = button_action(data)
    if hit is None:
        return UNNAMED
    return hit.rstrip(":_") or UNNAMED


# ---------------------------------------------------------------------------
# What a tap is ALLOWED to do.
# ---------------------------------------------------------------------------
#
# `_handle_callback` carried this as `_DESTRUCTIVE_CB_PERM`, a five-literal
# dict LOCAL to a 1,400-line method — so nothing could read the rule an
# operator is gated by, which is `vault_fix_hint`'s defect one surface over
# ("the map was nested in a 150-line method, so nothing could read the
# instruction an operator is given"). It sat in the same function as
# `BUTTON_ACTIONS`, whose docstring twenty lines up says a map kept in step by
# hand is the `/setllm` ten-of-eleven shape and which is AST-pinned for exactly
# that reason. The lesson reached the TRANSCRIPT table and not the PERMISSION
# table one screen away.
#
# What the missing row cost, driven on the shipped default
# (`PER_USER_LIVE_ENABLED=False`): a caller whose role is `viewer` — which
# holds `portfolio`, so the positions card renders for them, and not `trade` —
# taps the ordinary Close button, correctly tagged with their OWN uid so the
# IDOR guard passes by design, and `close_position` is awaited on the
# OPERATOR's live executor. `/liveclose` is the same call on the same account
# and is `@guard("admin")`; `closeall_confirm` is in the map below under
# `halt`. Closing EVERY position needed a permission and closing ONE needed
# none, which is the tell that the door was missed rather than weighed.
#
# `_caller_executor` is why the second layer people reason from is not there.
# `test_callback_owner_guard_is_fail_closed.py` records `pos_close_` as
# fail-open on an absent owner tag BY DESIGN, on the grounds that it "resolves
# the position through `user_portfolios.get(user_id)` and
# `_caller_executor(update)`, both keyed by the caller" — true of the CALL and
# false of the ACCOUNT, because that resolver's own docstring says that with
# per-user live off it is "ALWAYS the shared operator executor". This table
# does not reopen that decision: the tap driven above is correctly tagged, so
# the owner predicate never fires. Routing is one axis and ROLE is another, and
# only the first had been asked.

#: Rows gated HERE, before the dispatcher branches. Keyed by the
#: `BUTTON_ACTIONS` row, matched the same way (whole or prefix, longest wins).
CALLBACK_PERMISSION: dict[str, str] = {
    "closeall_confirm": "halt",
    "emergency_confirm": "halt",
    "mode_": "mode",
    # Guardian intent-policy apply buttons change enforcement -> the same gate
    # as a strategy-mode change. `policy_cancel` is a branch of its own below.
    "policy_": "mode",
    # Found by widening the transcript walk one hop: the dispatcher
    # branches on `policy_` and the helper distinguishes enforce from
    # shadow, so a SHADOW apply and an ENFORCE apply recorded as one
    # word -- `policy_cancel`'s own argument, one row over. It needs the
    # same gate as its prefix, and spelling it here keeps longest-match
    # from ever reading it as something weaker.
    "policy_apply_enforce": "mode",
    # THE ROW THAT WAS MISSING. `trade` and not `admin`, because the same
    # button closes the caller's OWN paper book in paper mode and `paper` holds
    # `trade` -- gating on admin would take a paper user's own positions away
    # from them, which is the over-strict half of a fix being its own defect.
    # `viewer` does not hold it, which is the driven hole shut. The LIVE half
    # needs more than a role and is checked in the branch: see
    # `callback_handler`'s `pos_close_` block, which mirrors the OPEN door's
    # H-18 authority.
    "pos_close_": "trade",
    "risk_emergency_stop": "halt",
    "risk_pause": "halt",
    "risk_safe_mode": "halt",
}

#: Every other row, with the reason it needs no gate HERE. A reason rather than
#: a bare set, because "gated somewhere else" and "harmless" are different
#: facts and only the first can stop applying: `command_gates.py`'s rule, where
#: an unrecognised spelling reads as `none` and a `none` row must say why.
CALLBACK_NO_PERMISSION: dict[str, str] = {
    "admit:": "admin-gated in the branch (`_is_admin`), attributed and audited",
    "closeall_cancel": "a cancellation: it changes nothing",
    "confirm:": "gated in the branch by the owner tag, `_is_admin` and H-18 "
                "`_can_trade_live` -- the OPEN door",
    "duel:": "a pick in the caller's own duel round",
    "emergency_cancel": "a cancellation: it changes nothing",
    "lang:": "the caller's own display language",
    "latest_signal": "delegates to `_cmd_latest_signal`, which carries its own "
                     "`@guard`",
    "nav:": "navigation between panes; each pane re-runs its own guarded read",
    "open_warroom": "navigation: opens the war-room pane, reads nothing new",
    "orders": "delegates to `_cmd_orders`, which carries its own `@guard`",
    "pane:": "navigation between panes; each pane re-runs its own guarded read",
    "performance": "delegates to `_cmd_performance`, which carries its own "
                   "`@guard`",
    "policy_cancel": "a cancellation: it changes nothing",
    "pos_details_": "a read of one position card",
    "positions": "delegates to `_cmd_open_positions`, which carries its own "
                 "`@guard`",
    "reject:": "owner-checked (`_callback_owner_ok`); rejecting an idea places "
               "nothing",
    "risk_control": "delegates to `_cmd_risk`, which carries its own `@guard`",
    "scan_confirm:": "a refusal: a button from a scan card sent before the "
                     "card's buttons named its idea; it places nothing",
    "scan_limit:": "a refusal: a button from a scan card sent before the "
                   "card's buttons named its idea; it arms nothing",
    "scan_reject:": "a rejection: it places nothing",
    "setlimit:": "owner-checked (`_callback_owner_ok`); the placement behind "
                 "it is `confirm:`, which is gated",
    "signal_watch_": "the caller's own watch list",
    "stance_keep": "keeping the current stance: it changes nothing",
    "strategy_mode": "delegates to `_cmd_strategy`, which carries its own "
                     "`@guard`; the change itself is `mode_`",
    "yld:": "gated in the branch by `_earn_button_account`, which requires the "
            "presser to hold `stake` and to resolve to a usable account",
    "yldf:": "gated in the branch by `_earn_button_account`, which requires "
             "the presser to hold `stake` and to resolve to a usable account",
}


def required_permission(data: object) -> Optional[str]:
    """The permission this tap needs before the dispatcher may branch, or None.

    LONGEST match, for the reason `button_action` gives: the dispatcher tests
    some literals whole and some as prefixes, and `policy_cancel` is a branch
    of its own inside `policy_` -- reading a cancellation as a policy change
    would refuse a button that changes nothing.

    A row this build does not name answers None, which is the same answer a
    declared-harmless row gives. That is deliberate and is the reason
    `tests/test_a_close_button_needs_a_permission.py` requires every
    `BUTTON_ACTIONS` row to appear in exactly one of the two tables: the
    RUNTIME cannot tell "nobody decided" from "decided harmless", so the
    decision is forced where it can be read rather than inferred here, where
    the quiet answer would be an acquittal.
    """
    if not isinstance(data, str):
        return None
    hit: Optional[str] = None
    for row in CALLBACK_PERMISSION:
        if data == row or data.startswith(row):
            if hit is None or len(row) > len(hit):
                hit = row
    if hit is None:
        return None
    # A longer declared-harmless row wins over a shorter gated one, so
    # `policy_cancel` is not read as `policy_`.
    for row in CALLBACK_NO_PERMISSION:
        if (data == row or data.startswith(row)) and len(row) > len(hit):
            return None
    return CALLBACK_PERMISSION[hit]
