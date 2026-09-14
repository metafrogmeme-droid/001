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
    # /quant is @guard("analyze") — the same read-only question about one
    # symbol that /analyze answers, one statistical layer deeper, so it reuses
    # a permission trader and paper already hold. The reason it sat unwired was
    # recorded as a TIER decision ("a permission alone would give away more
    # than deepscan for free"), and the tier gate is where that is now settled:
    # `quant_analyze` is `pro`, beside deepscan. Derived from the decorator,
    # like every entry above.
    "quant_analyze": "analyze",
    # Reachable from Telegram free text ("my journal", "trade log") and not
    # from web chat. Present here because this table is the FACT — /journal and
    # /daily_report are both @guard("journal") — and absent from
    # WEB_CHAT_SKILLS below, which is where reachability is decided.
    "trade_journal": "journal",
    # /postmortem is @guard("portfolio"): ONE of the caller's own closed
    # trades, read off the same book get_portfolio describes.
    "trade_postmortem": "portfolio",
    # /orders is @guard("portfolio"), the permission `get_portfolio` already
    # reuses: the same account, one column over (what is RESTING rather than
    # what is HELD). The intent router had named `get_orders` for months
    # while no such skill was registered — Telegram special-cased the name
    # to the command, the web aliased it to `get_portfolio`, and the chat
    # tool catalogue had nothing. Derived from the decorator, like every
    # entry above; the web set below picks it up by construction.
    "get_orders": "portfolio",
    # `halt` IS the fact and it is deliberately in no transport's set. See
    # DANGEROUS_SKILLS.
    "halt": "halt",
}

#: The same skills, written for a PERSON: one short phrase naming what the
#: caller gets if they ask for it.
#:
#: A COLUMN ON THIS TABLE, not a map in the module that renders the card,
#: because a map somewhere else is the `/setllm` ten-of-eleven shape — a skill
#: added later would simply be missing from it and nothing would say so.
#: `tests/test_the_bot_can_say_what_it_does.py` pins the key sets equal, so a
#: new skill fails here rather than vanishing from the answer to "what can you
#: do?".
#:
#: These are NOT the tool descriptions. Those are written AT the model ("Call
#: this for any question about…", "the caller's OWN account"), which is right
#: there and reads as a stranger's notes in a card shown to the caller.
SKILL_SAYS: dict[str, str] = {
    "analyze_asset": "a full read of one asset — structure, levels and the "
                     "setup the engine sees",
    "check_risk": "your drawdown against the limit, your exposure, and whether "
                  "new entries are allowed right now",
    "costs": "today's model and API spend against the daily budget",
    "deepscan": "a deep scan across the whole symbol universe with chart "
                "patterns",
    "get_portfolio": "your balance, open positions and closed-trade record",
    "get_orders": "your resting limit orders and stop/take-profit triggers, as "
                  "the exchange reports them",
    "learning": "the learning system's readiness dashboard",
    "macro_calendar": "the macro-event calendar and what the entry gate reads "
                      "from it",
    "optimize": "parameter optimisation over the recorded history",
    "patterns": "recurring market patterns the learning system has measured",
    "playbook": "the engine's execution playbook",
    "pro_scan": "a scan tuned to one timeframe — scalp, intraday or swing",
    "proposals": "improvement proposals the learning loop is holding for review",
    "rejected_trades": "the most recent ideas the risk gate rejected, with the "
                       "reason",
    "run_backtest": "a backtest of a strategy over recorded data",
    "run_strategy": "one strategy run against the current market",
    "scan_market": "a live scan of the exchange for movers and volume anomalies",
    "walk_forward": "a walk-forward validation of a strategy",
    "whynot": "why the engine did not take a trade on a symbol",
    "check_event_risk": "macro-event risk for one symbol over the next window",
    "compliance_status": "restricted jurisdictions and the consent ledger "
                         "summary",
    "macro_brief": "the macro gate's posture and the size multiplier it is "
                   "applying to new entries",
    "quant_analyze": "the statistical read on one symbol — memory, trend "
                     "strength and a volatility forecast",
    "trade_journal": "your most recent closed trades",
    "trade_postmortem": "a post-mortem of one of your closed trades, read from "
                        "the record",
    # `halt` is in SKILL_PERMISSION because that table is the FACT, and in no
    # transport's reachable set. It is named here for the same reason: the key
    # sets are pinned equal, and an exemption would be the hole.
    "halt": "stop the engine (operator only, and never from chat — the command "
            "owns that authority)",
}

# What web chat may run. Byte-identical to the dict this replaced — the
# `halt` omission there was load-bearing and is preserved by construction
# rather than by remembering.
WEB_CHAT_SKILLS: frozenset[str] = frozenset(SKILL_PERMISSION) - {"halt", "trade_journal"}

#: Routed intents the web answers from a READING rather than a registered
#: skill, and the permission each needs.
#:
#: DERIVED, like every entry above: the permission is the one on the `@guard`
#: of the Telegram command that answers the same question. `status` is here
#: because the web used to ALIAS it to `get_portfolio` — a question about the
#: engine answered with the account card, and gated by the `portfolio`
#: permission rather than its own. `pending` holds neither, but `help` and
#: `status` are not held by the same roles, so answering a routed intent
#: ABOVE the permission check (which is where the unavailable notice sits)
#: would remove the gate entirely.
WEB_ROUTED_PERMISSION: dict[str, str] = {
    "status": "status",
    # The three reads the website's own intercepts answer and Telegram
    # renders only as slash commands: routed intents on both surfaces now,
    # each under the permission on the `@guard` of the command that renders
    # the same seam (`networth_card_text`, `rwa_card_text`,
    # `research_card_text`). `exposure` is deliberately NOT here: "whats my
    # exposure" is `check_risk`'s, a pinned routing.
    "networth": "networth",
    "rwa": "rwa",
    "research": "research",
    # The website chat's own cards, as commands: `_cmd_nft`, `_cmd_spot` and
    # `_cmd_airdrops` render the card the web intercept answers with, each
    # under a permission of its own name, held by trader, paper and viewer —
    # public market facts and a curated catalogue, no account read in any.
    "nft": "nft",
    "spot": "spot",
    "airdrops": "airdrops",
}

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
    # Typed "emergency stop" used to reach `_cmd_halt` — unconfirmed, and it
    # does NOT flatten — while the command it names shows an operator-checked
    # CONFIRM STOP card that does. The phrase gets the card.
    "emergency_stop": "_cmd_emergency_stop",
    # "pause my trading" / "stop my bot": a `my`-scoped request is not a
    # fleet request. `_cmd_pause` is scope-aware — the caller's own engine
    # under per-user live, an honest refusal otherwise — and resumable.
    "pause": "_cmd_pause",
}


def permission_for(skill_name: str) -> str | None:
    """The permission `skill_name` needs, or None when nothing declares one.

    None means REFUSE. A skill added later is unreachable from chat until
    somebody decides what it needs — the same fail-closed direction the web map
    chose, and for the same reason: the alternative is this defect arriving
    again, silently.
    """
    return SKILL_PERMISSION.get(skill_name)
