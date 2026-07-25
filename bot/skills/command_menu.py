"""Curated Telegram command menus — what pops up when a user types "/".

The bot registers ~60 slash commands but never told Telegram about any of them,
so the "/" autocomplete was empty and users had to memorise commands (or read
/help). These curated, role-aware menus fix that: a short, friendly essentials
list for everyone, and a fuller operator list surfaced only in the operator's
chat. The bot's natural-language routing still works — the menu just makes the
common actions discoverable.

Each entry is (command, description). Descriptions stay < 256 chars and lead
with a verb so the "/" list reads like a menu of actions. Every command here is
validated by tests against the handler's actual registered command set, so a
renamed/removed command can never leave a dead menu entry.
"""

from __future__ import annotations

from typing import List, Tuple

# Shown to EVERY user — the high-frequency essentials. Kept short on purpose:
# a wall of 60 commands is worse than a curated dozen.
DEFAULT_MENU: List[Tuple[str, str]] = [
    ("start", "👋 Register & see where things stand"),
    ("scan", "🔎 Scan the market for the best setups"),
    ("analyze", "🔬 Deep-dive one coin — e.g. /analyze SOL"),
    ("portfolio", "💼 Your equity, positions & win rate"),
    ("performance", "📊 Your PnL & trade stats"),
    ("open_positions", "📈 Your open positions"),
    ("orders", "🧾 Your open orders"),
    ("signals", "📡 Latest signals & why they fired"),
    ("news", "📰 Breaking news + alerts on your positions"),
    ("share", "🗒️ Share a note with your agent (forward a newsletter)"),
    ("mynotes", "📒 See the notes you've shared with your agent"),
    ("risk", "🛡 Risk status & circuit breaker"),
    ("watch", "🔔 Alert me when a symbol sets up"),
    ("connect", "🔑 Link your exchange to trade live"),
    ("help", "❓ All commands & how to talk to the bot"),
]

# Extra controls surfaced ONLY in the operator/admin chat (on top of the
# essentials above). These are the live-trading and administration levers.
ADMIN_EXTRA_MENU: List[Tuple[str, str]] = [
    ("resume", "▶️ Resume trading (clear the breaker)"),
    ("pause", "⏸ Pause trading now"),
    ("drawdownlimit", "📉 Adjust the live drawdown cap"),
    ("venue", "🏦 Show or switch the trading venue"),
    ("classpf", "📊 Live PnL by asset class"),
    ("funding", "📡 Funding rates across venues"),
    ("parity", "📏 Live vs backtest parity report"),
    ("golive", "🔥 Arm live trading"),
    ("livebalance", "💰 Live exchange balance"),
    ("livepositions", "📌 Live exchange positions"),
    ("closeall", "⛔ Close every open position"),
    ("readiness", "✅ Live-readiness checklist"),
    ("flags", "🚩 Feature flags & their state"),
    ("gates", "🚦 Why signals are being gated"),
    ("users", "👥 Manage users"),
    ("health", "🩺 System health"),
]


def _dedupe_keep_order(pairs: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    seen = set()
    out = []
    for name, desc in pairs:
        if name in seen:
            continue
        seen.add(name)
        out.append((name, desc))
    return out


def default_commands() -> List[Tuple[str, str]]:
    """The menu shown to every user."""
    return _dedupe_keep_order(DEFAULT_MENU)


def admin_commands() -> List[Tuple[str, str]]:
    """The operator menu: essentials first, then the admin controls."""
    return _dedupe_keep_order(DEFAULT_MENU + ADMIN_EXTRA_MENU)


def suggest(name: str, known: List[str], limit: int = 3) -> List[str]:
    """Closest known commands to a mistyped one — the "did you mean" list.

    Ranks by prefix/substring kinship first (what people actually mistype:
    /positions for /open_positions, /pnl for /performance) and then by edit
    distance, so a near-miss beats an unrelated command that merely shares
    letters. Returns [] when nothing is close enough — an honest "I don't
    know that one" beats a confident wrong guess.
    """
    import difflib

    n = (name or "").strip().lower().lstrip("/")
    if not n:
        return []
    exact_kin = [k for k in known if k.startswith(n) or n in k]
    close = difflib.get_close_matches(n, known, n=limit * 2, cutoff=0.6)
    out: List[str] = []
    for k in exact_kin + close:
        if k not in out:
            out.append(k)
        if len(out) >= limit:
            break
    return out


def unknown_command_reply(name: str, known: List[str], *, is_admin: bool = False) -> str:
    """The message for a slash command the bot does not have.

    Telegram silently swallowed these: the free-text handler excludes
    commands, so a typo produced NO response at all — the single biggest
    source of "the commands don't work". This always answers, points at the
    nearest real command, and ends somewhere useful.
    """
    safe = (name or "").strip().lstrip("/")[:32]
    safe = "".join(ch for ch in safe if ch.isalnum() or ch in "_-")
    lines = [f"🤔 I don't have a <b>/{safe}</b> command."] if safe else ["🤔 I don't know that command."]
    hits = suggest(safe, known)
    if hits:
        lines.append("Did you mean " + " · ".join(f"/{h}" for h in hits) + "?")
    lines.append("")
    lines.append("Tap <b>/</b> for the menu, or /help for everything — "
                 "/help trading, /help scan and /help portfolio jump straight "
                 "to a section. You can also just talk to me normally — no "
                 "command needed.")
    if not is_admin:
        lines.append("<i>Some commands are operator-only and won't appear for you.</i>")
    return "\n".join(lines)
