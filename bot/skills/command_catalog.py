"""Every Telegram command, grouped and described — the source of /help.

The bot registers 125 commands. /help documented FIVE of them, and the "/"
menu shows a curated 15, so ~110 working features were discoverable only by
word of mouth. That is the whole of the operator's report: "too many
commands, some don't work or it's not clear what they do."

Nothing here is cosmetic: a test asserts this catalogue and the handler's
registered command list match EXACTLY, so a command can never again ship
undocumented, and a retired command can never linger in the docs.

Each entry is (command, description). Groups carry an audience:
  "user"  — everyone sees it
  "admin" — operator-only; hidden from normal users, who cannot run it
            anyway (showing it is what makes commands feel "broken").
"""

from __future__ import annotations

from typing import Dict, List, Tuple

Group = Tuple[str, str, List[Tuple[str, str]]]   # (title, audience, entries)

GROUPS: List[Group] = [
    ("🚀 Start here", "user", [
        ("start", "register and see where things stand"),
        ("help", "this reference"),
        ("dashboard", "open the web dashboard"),
        ("connect", "link your own exchange account"),
        ("exchange", "your linked-account status (never shows keys)"),
        ("disconnect", "remove your linked credentials"),
        ("lang", "switch language"),
        ("version", "bot version and mode"),
        ("health", "system health"),
        ("status", "engine status right now"),
    ]),
    ("📈 Trading", "user", [
        ("trade", "place a trade — /trade buy SOL 71.42 sl 70.05 tp 76.42"),
        ("paper", "practise risk-free with virtual funds"),
        ("golive", "enable live trading (double confirmation)"),
        ("open_positions", "your open positions"),
        ("livepositions", "live exchange positions and pending orders"),
        ("orders", "your open/pending orders"),
        ("liveclose", "close one live position — /liveclose <id>"),
        ("latest_signal", "pending signals with action buttons"),
        ("autoconfirm", "view or set the auto-confirm threshold"),
        ("emergency_stop", "stop everything (asks to confirm)"),
        ("pause", "pause trading (circuit breaker on)"),
        ("resume", "resume trading"),
        ("halt", "halt the engine"),
        ("reset", "reset the circuit breaker"),
        ("buy", "legacy — use /trade buy"),
        ("sell", "legacy — use /trade sell"),
    ]),
    ("🔎 Scan & analyse", "user", [
        ("scan", "scan the market for setups"),
        ("fullscan", "full 67-symbol scan"),
        ("deepscan", "deep scan with chart + candle patterns"),
        ("forcescan", "scan now, bypassing cooldown"),
        ("analyze", "deep-dive one coin — /analyze SOL"),
        ("alpha", "daily alpha insight card"),
        ("research", "cited research dossier for a symbol"),
        ("whynot", "why a trade was rejected"),
        ("patterns", "chart patterns the engine sees"),
        ("squeeze", "volatility squeeze status"),
        ("sweep", "liquidity sweep detection"),
        ("zones", "supply/demand zones"),
        ("momentum", "momentum scan"),
        ("dip", "dip scan"),
        ("scalp", "5m scalp scan"),
        ("intraday", "15m intraday scan"),
        ("swing", "4h swing scan"),
        ("stockscan", "tokenized US stock perps"),
        ("mode", "switch universe — solana / all / stocks"),
        ("session", "current trading session and its risk settings"),
    ]),
    ("🌍 Market context", "user", [
        ("macro", "macro backdrop and the next big event"),
        ("news", "headline radar + alerts on your positions"),
        ("funding", "live funding rates across venues"),
        ("fundingscan", "annualized funding, multi-venue"),
        ("arb", "funding-arb paper tracker"),
        ("rwa", "tokenized real-world-asset radar"),
        ("crossasset", "cross-asset correlation context"),
    ]),
    ("💼 Portfolio & record", "user", [
        ("portfolio", "equity, positions and win rate"),
        ("performance", "your PnL and trade stats"),
        ("networth", "cross-venue net worth snapshot"),
        ("exposure", "net per-asset exposure"),
        ("risk", "risk status and circuit breaker"),
        ("signals", "per-pair signal stats"),
        ("rejected", "signals risk turned down"),
        ("journal", "weekly trade journal"),
        ("daily_report", "daily trading report"),
        ("classpf", "performance by asset class"),
        ("holdtime", "hold-time analytics"),
        ("equitycurve", "equity-curve breaker status"),
        ("costs", "trading costs breakdown"),
    ]),
    ("🔔 Alerts & notes", "user", [
        ("watch", "proactive alerts for this chat"),
        ("share", "save a note your agent can use"),
        ("mynotes", "the notes you've shared"),
        ("agent", "your agent's posture, in plain language"),
    ]),
    ("🧪 Research & tuning", "user", [
        ("backtest", "run a backtest"),
        ("walkforward", "walk-forward validation"),
        ("montecarlo", "Monte Carlo risk simulation"),
        ("calibration", "learning overlays and calibration"),
        ("learn", "what the engine has learned"),
        ("optimize", "parameter optimization"),
        ("proposals", "pending tuning proposals"),
        ("strategy", "active strategy and regime routing"),
        ("playbook", "full system playbook briefing"),
        ("run", "run a named strategy preset"),
    ]),
    ("🛡 Guardian (operator)", "admin", [
        ("guardian", "the Guardian console"),
        ("twin", "portfolio digital twin — stress tests"),
        ("sentinel", "systemic risk sentinel"),
        ("escape", "emergency exit PLAN (read-only)"),
        ("policy", "intent compiler — authority envelope"),
        ("anchor", "ERC-8004 identity anchoring"),
        ("vault", "secret-protection status (names only)"),
        ("backup", "verifiable backups of critical state"),
        ("readiness", "is the learning loop validated enough to apply"),
    ]),
    ("🚦 Engine ops (operator)", "admin", [
        ("gates", "per-gate pass/fail telemetry"),
        ("flags", "deep-audit opt-in flags"),
        ("shadow", "counterfactual shadow book"),
        ("audit", "nightly self-audit report"),
        ("parity", "live ↔ backtest parity"),
        ("attribution", "which indicators drive wins"),
        ("slippage", "slippage statistics"),
        ("accounts", "risk snapshot per account"),
        ("closeall", "flatten every open position"),
        ("drawdownlimit", "override the drawdown limit"),
        ("leverage", "the standard leverage"),
        ("venue", "show or switch the trading venue"),
        ("livebalance", "real exchange balance"),
    ]),
    ("👥 Users & access (operator)", "admin", [
        ("users", "list registered users"),
        ("approve", "approve a user"),
        ("revoke", "revoke a user"),
        ("grant_live", "allow a user to trade live"),
        ("revoke_live", "restrict a user to paper"),
        ("set_tier", "change a user's tier"),
        ("setcap", "cap a user's margin"),
        ("weblive", "web live-trading readiness"),
        ("broadcast", "message all marketing channels"),
        ("channel", "manage channel auto-posting"),
    ]),
    ("🧠 LLM & yield (operator)", "admin", [
        ("llmstatus", "current LLM provider and key fingerprint"),
        ("llmtiers", "multi-tier routing configuration"),
        ("llmab", "LLM shadow A/B report"),
        ("llmreset", "revert to .env LLM settings"),
        ("setllm", "switch LLM provider at runtime"),
        ("settier", "per-tier LLM routing"),
        ("ultra", "ULTRA admin routing"),
        ("setexchange", "repair exchange credentials"),
        ("setgateway", "repair the web gateway secret"),
        ("yield", "idle-asset yield radar (read-only)"),
        ("idleyield", "cross-source best-rate scan"),
        ("stake", "put idle stables into flexible Earn"),
        ("unstake", "redeem Earn back to trading margin"),
    ]),
]


def all_entries() -> Dict[str, Tuple[str, str, str]]:
    """command -> (group title, audience, description)."""
    out: Dict[str, Tuple[str, str, str]] = {}
    for title, audience, entries in GROUPS:
        for name, desc in entries:
            out[name] = (title, audience, desc)
    return out


def help_sections(is_admin: bool = False) -> List[Tuple[str, List[Tuple[str, str]]]]:
    """The groups this caller can actually use.

    Operator-only groups are hidden from normal users on purpose: a command
    you are refused is indistinguishable from a command that is broken, and
    that confusion is most of why the surface felt untrustworthy.
    """
    return [(title, entries) for title, audience, entries in GROUPS
            if is_admin or audience == "user"]


def render_help(is_admin: bool = False, *, per_message: int = 3400) -> List[str]:
    """Render /help as one or more Telegram-sized HTML messages.

    Telegram hard-caps a message at 4096 characters and the full operator
    reference is far longer, so this splits on GROUP boundaries — a group is
    never torn across two messages.
    """
    chunks: List[str] = []
    buf = ""
    for title, entries in help_sections(is_admin):
        block = f"\n<b>{title}</b>\n" + "\n".join(
            f"  /{name} — {desc}" for name, desc in entries) + "\n"
        if buf and len(buf) + len(block) > per_message:
            chunks.append(buf.rstrip())
            buf = ""
        buf += block
    if buf.strip():
        chunks.append(buf.rstrip())
    return chunks
