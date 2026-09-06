"""Skill → the permission its equivalent Telegram command requires.

ONE table, because the alternative was tried and it drifted into a hole.

`bot/web/user_gateway.py` grew this map when web chat was found executing ANY
registry skill by name: a stranger who signed up on the website could POST
"halt the bot" to `/api/chat` and trip the global circuit breaker. The fix was
correct and it was scoped to one transport. The Telegram free-text path —
`_handle_message` → `intent_router` → `skill.execute(...)` — reaches the same
skills by the same English words and never got the same treatment, so "stop
trading" typed into the chat still ran `HaltSkill` with no role check at all.

That is the same defect twice, and a second copy of this table is how it would
be a third time. So the FACT (which permission a skill needs) lives here once,
and each transport declares only its own reachable SET below.

DERIVED, NOT INVENTED. Every entry is the permission on the `@guard(...)`
decorator of the command that dispatches the same skill.
`tests/test_free_text_obeys_the_role_gate.py` re-derives the pairing from
`telegram_handler.py`'s decorators and fails if this table disagrees.
"""
from __future__ import annotations

SKILL_PERMISSION: dict[str, str] = {
    "analyze_asset": "analyze",
    "check_risk": "risk",
    "costs": "costs",
    "deepscan": "deepscan",
    "get_portfolio": "portfolio",
    "learning": "learn",
    "macro_calendar": "macro",
    "optimize": "optimize",
    "patterns": "patterns",
    "playbook": "playbook",
    "pro_scan": "scan",
    "proposals": "proposals",
    "rejected_trades": "rejected",
    "run_backtest": "backtest",
    "run_strategy": "run",
    "scan_market": "scan",
    "walk_forward": "walkforward",
    "whynot": "rejected",
    # /eventrisk is @guard("macro") — the same read-only macro data /macro
    # serves, scoped to a symbol. Derived from the decorator, like every entry
    # above; test_free_text_obeys_the_role_gate re-derives it and fails on
    # disagreement.
    "check_event_risk": "macro",
    # /compliance is @guard("compliance"), a permission NO role but admin
    # holds. The card summarises the global consent ledger — other users'
    # grant/deny outcomes — so free text reaching it is fine and the role gate
    # is what refuses. Present here because this table is the FACT.
    "compliance_status": "compliance",
    # `macro_brief` is the macro gate's POSTURE — risk state, the size
    # multiplier being applied to new entries, whether the calendar is stale
    # or blind — where /macro (`macro_calendar`) is the calendar itself. The
    # same read-only macro data, so the permission /eventrisk reuses, and the
    # decorator this is derived from is /macro's own: the command it
    # advertised, `/macro`, was the calendar's, so it is a sub-mode of that
    # command (`/macro brief`) rather than a second name for one subject.
    # Chat reaches it too — free text through this table, tool calling
    # through bot/nlp/chat_tools.py.
    "macro_brief": "macro",
    # Reachable from Telegram free text ("my journal", "trade log") and not
    # from web chat. Present here because this table is the FACT — /journal and
    # /daily_report are both @guard("journal") — and absent from
    # WEB_CHAT_SKILLS below, which is where reachability is decided.
    "trade_journal": "journal",
    # `halt` IS the fact and it is deliberately in no transport's set. See
    # DANGEROUS_SKILLS.
    "halt": "halt",
}

# What web chat may run. Byte-identical to the dict this replaced — the
# `halt` omission there was load-bearing and is preserved by construction
# rather than by remembering.
WEB_CHAT_SKILLS: frozenset[str] = frozenset(SKILL_PERMISSION) - {"halt", "trade_journal"}

# Skills a chat transport must never `execute()` directly, whatever the
# caller's role says.
#
# A role check is not enough for these, which is the whole lesson of H4's second
# half: `trader` HOLDS `halt`, so a permission gate would have let a vouched-for
# teammate stop trading for every account by typing three words. The command
# handlers carry a further operator check (`_is_operator` / `_control_scope`)
# that a bare `skill.execute()` skips entirely.
#
# So free text does not gate these — it ROUTES them to the guarded command and
# lets the real gate run. `_cmd_orders` was already reached that way from the
# intent router; this extends the same treatment to the one that matters.
DANGEROUS_SKILLS: dict[str, str] = {
    # skill -> the TelegramHandler method that owns its authority
    "halt": "_cmd_halt",
}


def permission_for(skill_name: str) -> str | None:
    """The permission `skill_name` needs, or None when nothing declares one.

    None means REFUSE. A skill added later is unreachable from chat until
    somebody decides what it needs — the same fail-closed direction the web map
    chose, and for the same reason: the alternative is this defect arriving
    again, silently.
    """
    return SKILL_PERMISSION.get(skill_name)
