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

from typing import Dict, List, Tuple

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


def unknown_command_reply(name: str, known: List[str], *, is_admin: bool = False,
                          lang: str = "en") -> str:
    """The message for a slash command the bot does not have.

    Telegram silently swallowed these: the free-text handler excludes
    commands, so a typo produced NO response at all — the single biggest
    source of "the commands don't work". This always answers, points at the
    nearest real command, and ends somewhere useful.
    """
    safe = (name or "").strip().lstrip("/")[:32]
    safe = "".join(ch for ch in safe if ch.isalnum() or ch in "_-")
    zh = lang == "zh"
    if safe:
        head = f"🤔 沒有 <b>/{safe}</b> 這個指令。" if zh else f"🤔 I don't have a <b>/{safe}</b> command."
    else:
        head = "🤔 我不認得這個指令。" if zh else "🤔 I don't know that command."
    lines = [head]
    hits = suggest(safe, known)
    if hits:
        joined = " · ".join(f"/{h}" for h in hits)
        lines.append((f"你是不是想用 {joined}？" if zh else f"Did you mean {joined}?"))
    lines.append("")
    lines.append(
        "點 <b>/</b> 查看選單，或用 /help 看全部 — /help trading、/help scan、"
        "/help portfolio 可直接跳到該分類。你也可以直接跟我對話，不需要指令。"
        if zh else
        "Tap <b>/</b> for the menu, or /help for everything — "
        "/help trading, /help scan and /help portfolio jump straight "
        "to a section. You can also just talk to me normally — no "
        "command needed.")
    if not is_admin:
        lines.append("<i>部分指令僅限操作員，不會出現在你的清單中。</i>" if zh else
                     "<i>Some commands are operator-only and won't appear for you.</i>")
    return "\n".join(lines)


# ── Traditional Chinese menu text ─────────────────────────────────────────
# Telegram registers menus PER LANGUAGE (set_my_commands(language_code=...)),
# so a Chinese client can get a Chinese "/" popup instead of English. Missing
# entries fall back to English per item, never to a blank menu row.
MENU_ZH: Dict[str, str] = {
    "start": "👋 註冊並查看目前狀態",
    "scan": "🔎 掃描市場尋找最佳機會",
    "analyze": "🔬 深入分析單一幣種 — 例如 /analyze SOL",
    "portfolio": "💼 你的權益、持倉與勝率",
    "performance": "📊 你的損益與交易統計",
    "open_positions": "📈 你的持倉",
    "orders": "🧾 你的掛單",
    "signals": "📡 最新訊號與觸發原因",
    "news": "📰 突發新聞與持倉提醒",
    "share": "🗒️ 分享筆記給你的代理",
    "mynotes": "📒 查看你分享過的筆記",
    "risk": "🛡 風險狀態與熔斷器",
    "watch": "🔔 有機會時提醒我",
    "connect": "🔑 連結交易所以進行實盤",
    "help": "❓ 所有指令與使用方式",
    "gates": "🚦 訊號被攔下的原因",
    "flags": "🚩 功能旗標與狀態",
    "readiness": "✅ 實盤就緒檢查表",
    "users": "👥 管理使用者",
    "health": "🩺 系統健康狀態",
    "closeall": "⛔ 平掉所有持倉",
    "livebalance": "💰 交易所實際餘額",
    "livepositions": "📌 交易所實盤持倉",
    "resume": "▶️ 恢復交易（解除熔斷）",
    "pause": "⏸ 立即暫停交易",
    "drawdownlimit": "📉 調整實盤回撤上限",
    "venue": "🏦 查看或切換交易場所",
    "classpf": "📊 各資產類別的實盤損益",
    "funding": "📡 各交易所的資金費率",
    "parity": "📏 實盤與回測一致性報告",
    "golive": "🔥 啟用實盤交易",
}


def localized(pairs: List[Tuple[str, str]], lang: str = "en") -> List[Tuple[str, str]]:
    """Menu entries in `lang`, falling back to the English text per item.

    Traditional Chinese lives in MENU_ZH above; the other dictionary
    languages come from the catalogue's locale files, which carry /help and
    this menu together so the two cannot drift apart. English, or a language
    with no menu text, is the English list unchanged — the caller compares
    against it to avoid registering a copy of English under another code.
    """
    if not lang or lang == "en":
        return pairs
    if lang == "zh":
        return [(name, MENU_ZH.get(name, desc)) for name, desc in pairs]
    from bot.skills.command_catalog import menu_desc
    return [(name, menu_desc(name, lang) or desc) for name, desc in pairs]
