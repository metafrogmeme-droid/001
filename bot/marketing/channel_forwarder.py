"""
RUNECLAW Channel Forwarder — auto-post signals, results, and reports
to a public Telegram group/channel.

Posts are cleaned up: no confirm/reject buttons, no internal IDs,
no sensitive data. Public-facing marketing content only.
"""

from __future__ import annotations

import asyncio
import html
import json
import threading
from datetime import datetime
from typing import Any, Callable, Iterable, Optional

from bot.core.signal_confidence import displayed_confidence
from bot.compat import UTC
from bot.marketing.public_text import scrub_money
from bot.utils.logger import audit, system_log

from bot.utils.atomic_write import atomic_write_json
from bot.utils.paths import state_path

#: The TRADE CLOSED headline, by the close's NET outcome
#: (`public_text.close_outcome`). A measured break-even and an outcome nobody
#: measured both get the neutral mark: neither is a win or a loss.
_CLOSE_ICON = {"win": "\U0001f3c6", "loss": "\U0001f4c9", "flat": "\u26aa"}
_CLOSE_ICON_UNKNOWN = "\u26aa"

# Persistent config file for group chat IDs
# Repo-root anchored (bot/utils/paths.py): cwd-relative meant the forwarder
# silently forgot every configured channel when started from elsewhere.
_CONFIG_PATH = state_path("data/channel_config.json")


def _decided_by_words(idea) -> str:
    """The footer's claim about who made the call, read off the idea.

    It said "AI-generated" on every call. Since the rule engine decides by
    default (`AnalyzerConfig.llm_decides`, 11 October) the model's read rides
    beside the call and is not used to make it, so on most calls the word was
    false. An idea whose source says nothing claims nothing.
    """
    from bot.core.analyzer import DECIDED_BY_LLM, DECIDED_BY_RULES, thesis_decided_by
    who = thesis_decided_by(getattr(idea, "model_provider", None))
    if who == DECIDED_BY_LLM:
        return "\U0001f916 AI-generated signal | "
    if who == DECIDED_BY_RULES:
        return "\u2699\ufe0f Rule-engine signal | "
    return ""


def _rr_words(idea) -> str:
    """Reward to risk, gross and net of the fees: `trade_costs.idea_rr_words`.

    The post computed its own gross ratio, so a 6.5x call that pays 5.7x after
    a taker round trip was published at 6.5x, and an unreadable stop printed
    a confident 0.0x. One reading now, the one every idea card uses.
    """
    from bot.core.trade_costs import idea_rr_words
    return idea_rr_words(idea, wrap=lambda s: f"<code>{s}</code>")


#: Under every call that names levels. A claim about the record, so it names
#: the record's sign and nothing it cannot back.
_NOT_ADVICE = ("Not investment advice. The record of these calls is net negative "
               "after costs to date.")


class ChannelForwarder:
    """Forwards cleaned-up bot content to public Telegram groups/channels.

    A LIVE CALL IS A MEMBER'S, IN A GROUP AS ON THE WEBSITE. A group is read
    by whoever is in it, registered or not, so a new call and the agent's
    open are posted as a TEASER: the market, and where a member reads the
    rest (a private chat with the bot). The full card goes only to a group a
    bot admin marked as a members' room (`/channel full <id>`), and a
    resolved trade (a close) is posted whole to every group, as the website
    publishes a resolved call. `app/lib/signal_access.js` is the same rule
    on the website.
    """

    def __init__(self) -> None:
        self._bot = None
        self._group_ids: set[int] = set()
        # Groups a bot admin marked as members' rooms: they get the full card.
        self._full_ids: set[int] = set()
        self._lock = threading.Lock()
        self._enabled = True
        self._load_config()

    # ── Config persistence ────────────────────────────────────

    def _load_config(self) -> None:
        if _CONFIG_PATH.exists():
            try:
                with open(_CONFIG_PATH) as f:
                    data = json.load(f)
                self._group_ids = set(data.get("group_ids", []))
                self._full_ids = set(data.get("full_ids", []))
                self._enabled = data.get("enabled", True)
            except (json.JSONDecodeError, OSError):
                pass

    def _save_config(self) -> None:
        atomic_write_json(_CONFIG_PATH, {
            "group_ids": list(self._group_ids),
            "full_ids": list(self._full_ids),
            "enabled": self._enabled,
        })

    # ── Setup ─────────────────────────────────────────────────

    def set_bot(self, bot) -> None:
        """Set the Telegram bot instance (called from start_monitor)."""
        self._bot = bot

    def add_group(self, chat_id: int) -> None:
        """Register a group/channel for auto-posting."""
        with self._lock:
            self._group_ids.add(chat_id)
            self._save_config()
        # The id is a person's or a room's address: the structured field is
        # redacted by the logger, a number in the message text is not.
        audit(system_log, "Marketing group added",
              action="channel_add", result="OK", data={"chat_id": chat_id})

    def remove_group(self, chat_id: int) -> None:
        """Remove a group from auto-posting."""
        with self._lock:
            self._group_ids.discard(chat_id)
            self._full_ids.discard(chat_id)
            self._save_config()

    def set_full(self, chat_id: int, full: bool) -> None:
        """Mark a registered group as a members' room (the full card) or not
        (the teaser). Only a bot admin reaches this (`/channel full|teaser`):
        which room is a members' room is the operator's decision about who
        reads a live call."""
        with self._lock:
            if full:
                self._full_ids.add(chat_id)
            else:
                self._full_ids.discard(chat_id)
            self._save_config()
        audit(system_log, "Marketing group set to "
              f"{'full calls' if full else 'teaser'}",
              action="channel_full" if full else "channel_teaser", result="OK",
              data={"chat_id": chat_id})

    @property
    def full_ids(self) -> list[int]:
        return [g for g in self._full_ids if g in self._group_ids]

    def _members_door(self) -> str:
        """Where a member reads the rest: the bot's private chat, as a link
        when the bot's handle reads, and in words when it does not."""
        try:
            handle = str(getattr(self._bot, "username", "") or "").lstrip("@")
        except Exception:  # noqa: BLE001 -- an uninitialised bot has no handle
            handle = ""
        if handle:
            return f'<a href="https://t.me/{html.escape(handle)}">@{html.escape(handle)}</a>'
        return "a private chat with the RUNECLAW bot"

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = enabled
            self._save_config()

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def group_count(self) -> int:
        return len(self._group_ids)

    @property
    def group_ids(self) -> list[int]:
        return list(self._group_ids)

    # ── Auto-detect group from message ────────────────────────

    def detect_group(self, chat_id: int, chat_type: str, chat_title: str = "") -> bool:
        """Auto-detect and register a group when the bot receives a message.
        Returns True if a new group was detected."""
        if chat_type not in ("group", "supergroup", "channel"):
            return False
        if chat_id in self._group_ids:
            return False
        self.add_group(chat_id)
        system_log.info("Auto-detected marketing group: %s", chat_title)
        return True

    # ── Post methods ──────────────────────────────────────────

    async def _post(self, text: str, disable_notification: bool = False,
                    *, members_text: Optional[str] = None) -> None:
        """Post to all registered groups. ``members_text``, when given, is the
        full card, and goes only to a group marked as a members' room; every
        other group gets ``text``."""
        if not self._bot or not self._enabled or not self._group_ids:
            return
        # Every method in this class writes to a PUBLIC group, so the §4
        # constraint belongs here rather than at each caller — a new post
        # method inherits it instead of having to remember it. Prices survive
        # (post_signal publishes Entry/SL/TP, and those are market facts);
        # anything else carrying a dollar sign does not.
        text, _removed = scrub_money(text)
        if members_text is not None:
            members_text, _removed_m = scrub_money(members_text)
            _removed += _removed_m
        if _removed:
            # Not a success. The scrubber is the backstop; a caller that
            # composed private text is the actual defect, and it stays visible.
            system_log.critical(
                "Marketing post carried %d dollar amount(s) to a PUBLIC group "
                "and they were stripped before sending. The caller is composing "
                "private text — fix it there.", _removed)
        full_ids = set(self._full_ids)
        for gid in list(self._group_ids):
            body = members_text if (members_text is not None and gid in full_ids) else text
            try:
                await self._bot.send_message(
                    chat_id=gid, text=body, parse_mode="HTML",
                    disable_notification=disable_notification,
                    disable_web_page_preview=True)
            except Exception as exc:
                system_log.debug("Channel post to %s failed: %s", gid, exc)

    async def post_signal(self, idea) -> None:
        """Post a new trade signal to the group (no buttons).

        A CALL IS POSTED ONCE. The engine's idea lapses after PENDING_IDEA_TTL
        and the next scan re-emits the same setup under a new id; each of those
        was posted here as a new RUNECLAW SIGNAL, so a channel read one setup
        as a run of calls. An idea that re-offers a call still pending
        (`repeat_of`, set by the engine's publish step) is not posted.
        """
        if not self._enabled or not self._group_ids:
            return
        if getattr(idea, "repeat_of", None):
            return
        try:
            d = "\U0001f7e2 LONG" if idea.direction.value == "LONG" else "\U0001f534 SHORT"
            asset = idea.asset
            rr = _rr_words(idea)
            sl_pct = abs(idea.entry_price - idea.stop_loss) / idea.entry_price * 100
            tp_pct = abs(idea.take_profit - idea.entry_price) / idea.entry_price * 100
            now = datetime.now(UTC).strftime("%H:%M UTC")

            _sep = "\u2500" * 18
            # The adaptive formatter: `:,.4f` published a PEPE signal's three
            # levels as `$0.0000` each.
            from bot.formatters.rich_cards import _fmt_price
            # THE PUBLIC RECORD, so it is the figure every floor in this
            # repo is defined against. `idea.confidence` is whatever the
            # calibration curve left on the field -- a win-rate estimate on a
            # different scale -- so this line published 31% for a signal whose
            # measured blend was 70%, under a bare word "Confidence" on a
            # channel with `AI-generated signal` beneath it. A hand-typed
            # ticket's stamped 1.0 published as a measured 100%.
            confidence = displayed_confidence(idea).text()
            tag = asset.split('/')[0] if '/' in asset else asset
            # The teaser names the market and the door; never the side.
            teaser = (
                f"\U0001f4e1 <b>RUNECLAW</b> — new call on <b>{html.escape(asset)}</b>\n\n"
                f"The side, entry, stop and target are for members while the "
                f"call is live: open {self._members_door()} to read it. "
                f"Every call is published in full once it resolves.\n\n"
                f"#RUNECLAW #{html.escape(tag)}"
            )
            msg = (
                f"\U0001f4e1 <b>RUNECLAW SIGNAL</b>\n"
                f"{_sep}\n\n"
                f"{d} <b>{asset}</b>\n\n"
                f"Entry: <code>{_fmt_price(idea.entry_price)}</code>\n"
                f"Stop Loss: <code>{_fmt_price(idea.stop_loss)}</code> ({sl_pct:.1f}%)\n"
                f"Take Profit: <code>{_fmt_price(idea.take_profit)}</code> ({tp_pct:.1f}%)\n"
                f"R:R: {rr}\n"
                f"Confidence: <code>{confidence}</code>\n\n"
                f"{_sep}\n"
                f"{_decided_by_words(idea)}{now}\n"
                # Specific levels are trade instructions; the image card has
                # carried this line for as long as it has existed, and the
                # out-of-sample test (docs/research/prereg/
                # signal_edge_stage2_2026-10/REPORT.md) is why the record is
                # named beside it.
                f"<i>{_NOT_ADVICE}</i>\n"
                f"#RUNECLAW #{asset.split('/')[0] if '/' in asset else asset}"
            )
            await self._post(teaser, members_text=msg)
        except Exception as exc:
            system_log.debug("post_signal error: %s", exc)

    async def post_trade_opened(self, levels: dict) -> None:
        """Post the agent's open: an `agent_feed.opened_levels` reading.

        The ROW's levels, not the idea's: the executor re-prices a limit and
        moves its stop and target with it, and the post is made when the
        position OPENS (`RuneClawEngine._announce_agent_open`), never when a
        limit is placed to rest. It is LIVE by construction: only the
        operator's live book is announced.
        """
        if not self._enabled or not self._group_ids:
            return
        try:
            d = "\U0001f7e2 LONG" if levels["direction"] == "LONG" else "\U0001f534 SHORT"
            asset = levels["symbol"]
            now = datetime.now(UTC).strftime("%H:%M UTC")

            _sep = "\u2500" * 18
            from bot.formatters.rich_cards import _fmt_price
            entry = _fmt_price(levels.get("entry"))
            if levels.get("entry_estimated") and levels.get("entry") is not None:
                entry = f"~{entry} (estimated: the venue stated no fill price)"
            msg = (
                f"\u2705 <b>TRADE OPENED</b>\n"
                f"{_sep}\n\n"
                f"{d} <b>{html.escape(asset)}</b> | \U0001f525 LIVE\n\n"
                f"Entry: <code>{entry}</code>\n"
                f"Stop Loss: <code>{_fmt_price(levels.get('stop'))}</code>\n"
                f"Take Profit: <code>{_fmt_price(levels.get('target'))}</code>\n\n"
                f"{_sep}\n"
                f"\U0001f916 RUNECLAW | {now}\n"
                f"#RUNECLAW #{html.escape(asset.split('/')[0] if '/' in asset else asset)}"
            )
            teaser = (
                f"\u2705 <b>RUNECLAW opened a position on {html.escape(asset)}</b> | LIVE\n\n"
                f"Its side and levels are for members while it is open: "
                f"{self._members_door()}. The result is posted here when it "
                f"closes.\n\n"
                f"#RUNECLAW #{html.escape(asset.split('/')[0] if '/' in asset else asset)}"
            )
            await self._post(teaser, members_text=msg)
        except Exception as exc:
            system_log.debug("post_trade_opened error: %s", type(exc).__name__)

    async def post_trade_closed(self, close_msg: str,
                                outcome: Optional[str] = None) -> None:
        """Post a trade close result to the group.

        ``outcome`` is ``public_text.close_outcome(close_data)``: the NET
        result's sign, or None when nobody measured it. The headline icon is
        read from it and from nothing else.
        """
        if not self._enabled or not self._group_ids:
            return
        try:
            # THE TROPHY WAS A REGEX FOR ANY "+N%" IN THE TEXT, and the text
            # leads with the price MOVE: a 20x close that moved +0.10% and lost
            # 0.40% on margin after fees posted "🏆 TRADE CLOSED" above its
            # own red dot. (It had keyed off "+$" before that, which §4 removes
            # from this surface.) The sign is the net's, handed in by the
            # caller that holds the close record, and a close whose net nobody
            # measured gets neither icon: colour is a claim.
            emoji = _CLOSE_ICON.get(outcome or "", _CLOSE_ICON_UNKNOWN)
            now = datetime.now(UTC).strftime("%H:%M UTC")

            _sep = "\u2500" * 18
            # READY HTML IN, NOTHING ESCAPED HERE. This used to escape the
            # message line by line, and it received
            # two different kinds of message: `public_close_line`'s HTML, and
            # the private plain-text `close_msg` when that returns None. The
            # escape is right for the second and wrong for the first, and by
            # this point nothing can tell them apart \u2014 so a live card went out
            # reading `\ud83d\udd34 <b>CLUSDT</b> LONG closed` with the tags printed.
            #
            # The boundary moved to the two producers instead:
            # `public_close_line` escapes the fields it interpolates, and
            # `alerts_monitor` escapes the plain-text fallback where it picks
            # it. Both hand this ready HTML.
            body = close_msg.strip()
            msg = (
                f"{emoji} <b>TRADE CLOSED</b>\n"
                f"{_sep}\n\n"
                f"{body}\n\n"
                f"{_sep}\n"
                f"\U0001f916 RUNECLAW | {now}\n"
                f"#RUNECLAW #TradeResult"
            )
            await self._post(msg)
        except Exception as exc:
            system_log.debug("post_trade_closed error: %s", exc)

    async def post_daily_report(self, report_text: str) -> None:
        """Post a daily performance summary."""
        if not self._enabled or not self._group_ids:
            return
        try:
            now = datetime.now(UTC).strftime("%Y-%m-%d")
            _sep = "\u2500" * 18
            msg = (
                f"\U0001f4ca <b>DAILY REPORT \u2014 {now}</b>\n"
                f"{_sep}\n\n"
                f"{report_text}\n\n"
                f"{_sep}\n"
                f"\U0001f916 RUNECLAW AI Trading\n"
                f"#RUNECLAW #DailyReport"
            )
            await self._post(msg)
        except Exception as exc:
            system_log.debug("post_daily_report error: %s", exc)

    async def post_scan_result(self, scan_text: str) -> None:
        """Post a market scan summary."""
        if not self._enabled or not self._group_ids:
            return
        try:
            now = datetime.now(UTC).strftime("%H:%M UTC")
            _sep = "\u2500" * 18
            msg = (
                f"\U0001f50d <b>MARKET SCAN</b>\n"
                f"{_sep}\n\n"
                f"{scan_text}\n\n"
                f"{_sep}\n"
                f"\U0001f916 RUNECLAW | {now}\n"
                f"#RUNECLAW #MarketScan"
            )
            await self._post(msg, disable_notification=True)
        except Exception as exc:
            system_log.debug("post_scan_result error: %s", exc)

    async def post_sleeve_call(
        self,
        call_html: str,
        *,
        skip: Optional[Iterable[int]] = None,
        claim: Optional[Callable[[int], bool]] = None,
        on_sent: Optional[Callable[[int], None]] = None,
        on_fail: Optional[Callable[[int, str], None]] = None,
    ) -> dict[str, Any]:
        """The paper sleeve's rebalance call, one group at a time.

        Returns ``{"sent", "failed", "held", "skipped_claimed"}``. ``failed``
        maps a group to the exception class. A send that raises is not in
        ``sent``. ``skip`` is the groups that already have this call. ``claim``
        false means another pass owns that group: it is not posted.
        ``on_sent`` runs after a send returns, before the next group, so a
        pass that stops there has the success on disk. The warning names the
        exception class, never the message.
        """
        sent: list[int] = []
        failed: dict[int, str] = {}
        base: dict[str, Any] = {"sent": sent, "failed": failed, "skipped_claimed": 0}
        if not self._enabled:
            return {**base, "held": "disabled"}
        if not self._bot:
            return {**base, "held": "no_bot"}
        if not self._group_ids:
            return {**base, "held": "no_groups"}
        text, removed = scrub_money(call_html)
        if removed:
            system_log.critical(
                "Marketing post carried %d dollar amount(s) to a PUBLIC group "
                "and they were stripped before sending. The caller is composing "
                "private text — fix it there.", removed)
        skip_ids: set[int] = set()
        for raw in skip or ():
            gid = raw if isinstance(raw, int) and not isinstance(raw, bool) else None
            if gid is not None:
                skip_ids.add(gid)
        targets: list[int] = []
        seen: set[int] = set()
        for raw in list(self._group_ids):
            gid = raw if isinstance(raw, int) and not isinstance(raw, bool) else None
            if gid is None or gid in skip_ids or gid in seen:
                continue
            seen.add(gid)
            targets.append(gid)
        if not targets:
            return {**base, "held": "already_sent"}
        skipped_claimed = 0
        for gid in targets:
            if claim is not None and claim(gid) is False:
                skipped_claimed += 1
                continue
            try:
                await self._bot.send_message(
                    chat_id=gid, text=text, parse_mode="HTML",
                    disable_web_page_preview=True)
            except asyncio.CancelledError:
                if on_fail is not None:
                    on_fail(gid, "")
                raise
            except Exception as exc:
                name = type(exc).__name__
                system_log.warning(
                    "Sleeve channel post to %s failed (%s)", gid, name)
                failed[gid] = name
                if on_fail is not None:
                    on_fail(gid, name)
            else:
                sent.append(gid)
                if on_sent is not None:
                    on_sent(gid)
        return {"sent": sent, "failed": failed, "held": "",
                "skipped_claimed": skipped_claimed}

    async def post_custom(self, text: str) -> None:
        """Post custom content (admin command)."""
        await self._post(text)
