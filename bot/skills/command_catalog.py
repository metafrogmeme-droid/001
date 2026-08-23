"""Every Telegram command, grouped and described — the source of /help.

The bot registers 126 commands. /help documented FIVE of them, and the "/"
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
        ("duel", "call the market before the agent does — free, no stake"),
        ("leaderboard", "the verified board — ratios and counts, re-derivable"),
        ("arena", "paper arena — season standings and the live tape"),
        ("dashboard", "open the web dashboard"),
        ("connect", "link your own exchange account"),
        ("exchange", "your linked-account status (never shows keys)"),
        ("livebalance", "your real exchange balance (your own account, read-only)"),
        ("disconnect", "remove your linked credentials"),
        ("linkwallet", "link a Solana wallet (read-only) for $RCLAW tier access"),
        ("lang", "switch language"),
        ("version", "bot version and mode"),
        ("health", "system health"),
        ("status", "engine status right now"),
    ]),
    ("📈 Trading", "user", [
        ("trade", "place a trade — /trade buy SOL 71.42 sl 70.05 tp 76.42"),
        ("paper", "practise risk-free with virtual funds"),
        ("mystrategy", "choose which strategy preset YOUR confirms run through"),
        ("venues", "choose which of your connected venues actually trade"),
        ("open_positions", "your open positions"),
        ("livepositions", "live exchange positions and pending orders"),
        ("orders", "your open/pending orders"),
        ("latest_signal", "pending signals with action buttons"),
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
        ("analyze", "deep-dive one coin — /analyze SOL"),
        ("alpha", "daily alpha insight card"),
        ("research", "cited research dossier for a symbol"),
        ("token", "contract detective — /token 0x… [chain]"),
        ("memeplan", "meme-buy preflight (never trades) — /memeplan <mint> [usd]"),
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
    ]),
    ("💼 Portfolio & record", "user", [
        ("portfolio", "equity, positions and win rate"),
        ("performance", "your PnL and trade stats"),
        ("networth", "cross-venue net worth snapshot"),
        ("exposure", "net per-asset exposure"),
        ("risk", "risk status and circuit breaker"),
        ("signals", "per-pair signal stats"),
        ("rejected", "signals risk turned down"),
        ("daily_report", "daily trading report"),
        ("classpf", "performance by asset class"),
        ("holdtime", "hold-time analytics"),
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
        ("learn", "what the engine has learned"),
        ("optimize", "parameter optimization"),
        ("proposals", "pending tuning proposals"),
        ("playbook", "full system playbook briefing"),
        ("run", "run a named strategy preset"),
    ]),
    ("🧪 Research & tuning (operator)", "admin", [
        # Both were documented for everyone and admin-only in fact. /calibration
        # REFITS the confidence overlays every trade decision then uses, and
        # /montecarlo runs over engine.portfolio._history — the shared operator
        # book, not the caller's.
        ("montecarlo", "Monte Carlo risk simulation (operator book)"),
        ("calibration", "learning overlays and calibration — refit mutates them"),
    ]),
    ("🛡 Guardian (operator)", "admin", [
        ("guardian", "the Guardian console"),
        ("twin", "portfolio digital twin — stress tests"),
        ("sentinel", "systemic risk sentinel"),
        ("escape", "emergency exit PLAN (read-only)"),
        ("approvals", "allowance X-ray — what can spend your tokens"),
        ("xray", "decode calldata before signing (read-only)"),
        ("policy", "intent compiler — authority envelope"),
        ("anchor", "ERC-8004 identity anchoring"),
        ("vault", "secret-protection status (names only)"),
        ("backup", "verifiable backups of critical state"),
        ("readiness", "is the learning loop validated enough to apply"),
    ]),
    ("🚦 Engine ops (operator)", "admin", [
        # MOVED FROM USER GROUPS. Each of these was documented for everyone
        # while its handler refused every non-admin — the catalogue was the
        # error, not the guard, because each one reads or mutates the SHARED
        # operator engine rather than the caller's own account:
        #   golive       flips RUNTIME.live_mode + the global compliance profile
        #   liveclose    closes on the operator executor when per-user live is off
        #   autoconfirm  sets the global auto-confirm threshold
        #   forcescan    clears the shared pending ideas and bypasses cooldown
        #   equitycurve  the shared risk engine's breaker state
        #   journal      engine.journal — the operator account's trades
        #   strategy     the global router's regime + vol state
        #   crossasset   admin-gated in the handler; market-wide, but widening
        #                access is a product call, not an audit fix
        ("golive", "enable live trading (double confirmation)"),
        ("liveclose", "close one live position — /liveclose <id>"),
        ("autoconfirm", "view or set the auto-confirm threshold"),
        ("forcescan", "scan now, bypassing cooldown"),
        ("equitycurve", "equity-curve breaker status"),
        ("journal", "weekly trade journal (operator account)"),
        ("strategy", "active strategy and regime routing"),
        ("crossasset", "cross-asset correlation context"),
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


def _localize(title: str, name: str, desc: str, lang: str) -> Tuple[str, str]:
    """(title, description) in `lang`, falling back to English per-item.

    Partial coverage is safe by construction: an untranslated command shows
    its English description rather than a blank line.
    """
    if lang == "zh":
        return GROUP_TITLES_ZH.get(title, title), DESC_ZH.get(name, desc)
    return title, desc


def render_help(is_admin: bool = False, *, per_message: int = 3400,
                lang: str = "en") -> List[str]:
    """Render /help as one or more Telegram-sized HTML messages.

    Telegram hard-caps a message at 4096 characters and the full operator
    reference is far longer, so this splits on GROUP boundaries — a group is
    never torn across two messages.
    """
    chunks: List[str] = []
    buf = ""
    for title, entries in help_sections(is_admin):
        shown_title = _localize(title, "", "", lang)[0]
        block = f"\n<b>{shown_title}</b>\n" + "\n".join(
            f"  /{name} — {_localize(title, name, desc, lang)[1]}"
            for name, desc in entries) + "\n"
        if buf and len(buf) + len(block) > per_message:
            chunks.append(buf.rstrip())
            buf = ""
        buf += block
    if buf.strip():
        chunks.append(buf.rstrip())
    return chunks


# Short keywords for "/help trading" style deep-dives. A wall of 125 commands
# is unreadable even when grouped, so the caller can ask for one section.
GROUP_KEYS: Dict[str, str] = {
    "start": "🚀 Start here",
    "trade": "📈 Trading",
    "trading": "📈 Trading",
    "scan": "🔎 Scan & analyse",
    "analyse": "🔎 Scan & analyse",
    "analyze": "🔎 Scan & analyse",
    "market": "🌍 Market context",
    "macro": "🌍 Market context",
    "portfolio": "💼 Portfolio & record",
    "record": "💼 Portfolio & record",
    "alerts": "🔔 Alerts & notes",
    "notes": "🔔 Alerts & notes",
    "research": "🧪 Research & tuning",
    "tuning": "🧪 Research & tuning",
    "guardian": "🛡 Guardian (operator)",
    "ops": "🚦 Engine ops (operator)",
    "engine": "🚦 Engine ops (operator)",
    "users": "👥 Users & access (operator)",
    "access": "👥 Users & access (operator)",
    "llm": "🧠 LLM & yield (operator)",
    "yield": "🧠 LLM & yield (operator)",
}


def render_group(keyword: str, is_admin: bool = False, lang: str = "en") -> str:
    """One group's commands, or an honest miss listing what CAN be asked for.

    Never silently returns nothing: an unrecognised keyword — or an operator
    group a normal user asked for — answers with the sections they can
    actually browse.
    """
    key = (keyword or "").strip().lower().lstrip("/")
    title = GROUP_KEYS.get(key)
    visible = dict(help_sections(is_admin))
    if title and title in visible:
        body = "\n".join(f"  /{n} — {_localize(title, n, d, lang)[1]}"
                         for n, d in visible[title])
        return f"<b>{_localize(title, '', '', lang)[0]}</b>\n{body}"
    offer = ", ".join(sorted({
        k for k, v in GROUP_KEYS.items() if v in visible
    }))
    head = (f"No <b>{key}</b> section." if key else "Which section?")
    return f"{head}\nTry: {offer}\n\nOr /help for everything."


# ── Traditional Chinese ───────────────────────────────────────────────────
# The bot speaks en/zh, so an English-only /help would have handed Chinese
# users a wall of 125 English lines — a regression dressed as a feature.
# Group titles and descriptions are translated here; anything missing falls
# back to English rather than rendering blank, so partial coverage is safe.
GROUP_TITLES_ZH: Dict[str, str] = {
    "🚀 Start here": "🚀 從這裡開始",
    "📈 Trading": "📈 交易",
    "🔎 Scan & analyse": "🔎 掃描與分析",
    "🌍 Market context": "🌍 市場背景",
    "💼 Portfolio & record": "💼 投資組合與紀錄",
    "🔔 Alerts & notes": "🔔 提醒與筆記",
    "🧪 Research & tuning": "🧪 研究與調校",
    "🧪 Research & tuning (operator)": "🧪 研究與調校（操作員）",
    "🛡 Guardian (operator)": "🛡 Guardian（操作員）",
    "🚦 Engine ops (operator)": "🚦 引擎營運（操作員）",
    "👥 Users & access (operator)": "👥 使用者與權限（操作員）",
    "🧠 LLM & yield (operator)": "🧠 LLM 與收益（操作員）",
}

DESC_ZH: Dict[str, str] = {
    "start": "註冊並查看目前狀態", "help": "本指令總覽", "dashboard": "開啟網頁儀表板",
    "duel": "搶在代理之前判斷行情 — 免費、無賭注",
    "leaderboard": "已驗證排行榜 — 僅比率與次數，可重新驗算",
    "arena": "模擬競技場 — 賽季排名與即時成交紀錄",
    "connect": "連結你自己的交易所帳戶", "exchange": "你的帳戶連結狀態（永不顯示金鑰）",
    "disconnect": "移除已連結的憑證",
    "linkwallet": "連結 Solana 錢包（唯讀）以取得 $RCLAW 等級權限",
    "lang": "切換語言", "version": "機器人版本與模式",
    "health": "系統健康狀態", "status": "引擎目前狀態",
    "trade": "下單 — /trade buy SOL 71.42 sl 70.05 tp 76.42", "paper": "用虛擬資金無風險練習",
    "golive": "啟用實盤交易（需二次確認）", "open_positions": "你的持倉",
    "livepositions": "交易所實盤持倉與掛單", "orders": "你的掛單",
    "liveclose": "平掉一筆實盤持倉 — /liveclose <id>", "latest_signal": "待處理訊號與操作按鈕",
    "autoconfirm": "查看或設定自動確認門檻", "emergency_stop": "緊急停止（會先確認）",
    "pause": "暫停交易（啟動熔斷）", "resume": "恢復交易", "halt": "停止引擎",
    "reset": "重設熔斷器", "buy": "舊指令 — 請用 /trade buy", "sell": "舊指令 — 請用 /trade sell",
    "scan": "掃描市場尋找機會", "fullscan": "完整 67 檔掃描", "deepscan": "含形態的深度掃描",
    "forcescan": "立即掃描，略過冷卻", "analyze": "深入分析單一幣種 — /analyze SOL",
    "alpha": "每日 alpha 洞察卡", "research": "附出處的研究報告", "whynot": "為何某筆交易被拒",
    "token": "合約偵查 — /token 0x… [鏈]",
    "memeplan": "迷因幣買入預檢（絕不下單）— /memeplan <mint> [usd]",
    "patterns": "引擎看到的圖表形態", "squeeze": "波動壓縮狀態", "sweep": "流動性掃蕩偵測",
    "zones": "供需區", "momentum": "動能掃描", "dip": "回調掃描", "scalp": "5 分鐘短打掃描",
    "intraday": "15 分鐘日內掃描", "swing": "4 小時波段掃描", "stockscan": "美股代幣化永續",
    "mode": "切換範圍 — solana / all / stocks", "session": "目前交易時段與風險設定",
    "macro": "宏觀背景與下一個重大事件", "news": "頭條雷達與持倉提醒",
    "funding": "跨交易所即時資金費率", "fundingscan": "年化資金費率，多交易所",
    "arb": "資金費率套利模擬追蹤", "rwa": "代幣化實體資產雷達", "crossasset": "跨資產相關性背景",
    "portfolio": "權益、持倉與勝率", "performance": "你的損益與交易統計",
    "networth": "跨平台淨資產快照", "exposure": "各資產淨曝險", "risk": "風險狀態與熔斷器",
    "signals": "各交易對訊號統計", "rejected": "被風控攔下的訊號", "journal": "每週交易日誌",
    "daily_report": "每日交易報告", "classpf": "依資產類別的績效", "holdtime": "持倉時間分析",
    "equitycurve": "權益曲線熔斷狀態", "costs": "交易成本明細",
    "watch": "為此對話開啟主動提醒", "share": "儲存一則代理可參考的筆記",
    "mynotes": "你分享過的筆記", "agent": "用白話說明你的代理狀態",
    "backtest": "執行回測", "walkforward": "前進式驗證", "montecarlo": "蒙地卡羅風險模擬",
    "calibration": "學習疊加與校準", "learn": "引擎學到了什麼", "optimize": "參數最佳化",
    "proposals": "待處理的調校提案", "strategy": "當前策略與市況路由",
    "playbook": "完整系統戰術簡報", "run": "執行指定策略預設", "mystrategy": "選擇你的機器人採用哪個策略預設",
    "venues": "選擇你已連接的哪些交易所實際下單",
    "guardian": "Guardian 主控台", "twin": "投資組合數位孿生 — 壓力測試",
    "sentinel": "系統性風險哨兵", "escape": "緊急退場計畫（唯讀）",
    "approvals": "授權額度 X 光——誰能動用你的代幣", "xray": "簽名前解讀 calldata（唯讀）",
    "policy": "意圖編譯器 — 授權範圍", "anchor": "ERC-8004 身分錨定",
    "vault": "機密保護狀態（僅名稱）", "backup": "關鍵狀態的可驗證備份",
    "readiness": "學習迴路是否已驗證到可套用",
    "gates": "各關卡通過/失敗遙測", "flags": "深度稽核選用旗標", "shadow": "反事實影子帳本",
    "audit": "每夜自我稽核報告", "parity": "實盤 ↔ 回測一致性", "attribution": "哪些指標帶來獲利",
    "slippage": "滑價統計", "accounts": "各帳戶風險快照", "closeall": "平掉所有持倉",
    "drawdownlimit": "覆寫回撤上限", "leverage": "標準槓桿", "venue": "顯示或切換交易場所",
    "livebalance": "交易所實際餘額",
    "users": "列出已註冊使用者", "approve": "核准使用者", "revoke": "撤銷使用者",
    "grant_live": "允許使用者實盤交易", "revoke_live": "限制使用者僅可模擬",
    "set_tier": "變更使用者等級", "setcap": "限制使用者保證金", "weblive": "網頁實盤就緒狀態",
    "broadcast": "發送訊息到所有行銷頻道", "channel": "管理頻道自動發文",
    "llmstatus": "目前 LLM 供應商與金鑰指紋", "llmtiers": "多層路由設定",
    "llmab": "LLM 影子 A/B 報告", "llmreset": "還原為 .env 的 LLM 設定",
    "setllm": "執行時切換 LLM 供應商", "settier": "各層級 LLM 路由", "ultra": "ULTRA 管理路由",
    "setexchange": "修復交易所憑證", "setgateway": "修復網頁閘道密鑰",
    "yield": "閒置資產收益雷達（唯讀）", "idleyield": "跨來源最佳利率掃描",
    "stake": "將閒置穩定幣投入活期 Earn", "unstake": "將 Earn 贖回為交易保證金",
}
