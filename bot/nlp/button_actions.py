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
