"""The share button, and the invite that rides with it.

The community layer already exists — arena, duel, leaderboard, copy, feed and
frame add up to 35 web endpoints. The bot exposes ONE community command
(`/duel`). `switch_inline_query` appears nowhere in the codebase, `/start`
throws away its deep-link payload, and `render_share_card` — a PNG renderer
whose docstring already carries a privacy contract — is reachable only from the
web gateway. So the community was not missing. Its door was.

WHAT MAY BE SHARED, and why this module exists rather than a forward button.

CLAUDE.md: *no dollar amounts on public, community, leaderboard or marketplace
payloads — percent, ratio and count only.* The operator's own close card is
private and correctly says `PnL: $+0.15`. A button that forwarded THAT card
would publish account size into a group chat, one tap, no warning.

So a share is a re-render, never a forward, and `assert_no_money` runs on the
way out — the audited `_MONEY` pattern from bot/marketing/public_text, not a
second copy of the rule. A future edit that adds a dollar figure here raises
instead of leaking. The guard belongs in the producer because the leak would
otherwise be silent and permanent: once it is in someone's group chat, it is
not retractable.

LEVERAGE IS PART OF THE CLAIM. A close can be reported two ways, and they are
not close: the TAO trade that prompted this was `+0.47%` of price movement and
`+9.47%` on margin at 20×. Publishing the second alone reads as skill and is
the number a reader will compare against their own unlevered results. So when
the shared figure is a leveraged one, the leverage and the underlying move go
with it. Percent is permitted by the dollar rule; an unqualified percent is
still capable of misleading, and that is the rule this repo actually cares
about.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional
from urllib.parse import quote

from bot.marketing.public_text import _MONEY

#: `t.me/<bot>?start=<payload>` — Telegram caps the payload at 64 chars and
#: allows only [A-Za-z0-9_-], which is why the code is validated rather than
#: trusted: an invalid one produces a link that silently drops the referral.
REF_PREFIX = "ref_"
_REF_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,56}$")

#: Telegram's native share sheet. A `url` button needs no inline mode, no
#: BotFather change and no public image host — `switch_inline_query` would
#: need all three, and would have shipped a button that opens an empty picker
#: until someone remembered to enable inline mode in BotFather.
_SHARE_BASE = "https://t.me/share/url"


class MoneyLeak(AssertionError):
    """Raised when public-bound text carries a dollar figure. Never caught."""


def assert_no_money(text: str) -> str:
    """Return the text, or raise if it carries a dollar amount.

    Reuses ``bot.marketing.public_text._MONEY`` deliberately. A second regex
    would be a second definition of "what counts as money", and the two would
    drift — which is the failure mode this repo spends most of its guard tests
    preventing.
    """
    hits = _MONEY.findall(text or "")
    if hits:
        raise MoneyLeak(
            f"share text carries {len(hits)} dollar figure(s) {hits[:3]} — "
            "public payloads are percent, ratio and count only")
    return text


def display_handle(raw: Any, width: int) -> str:
    """A handle shortened to ``width`` for a fixed-width column, VISIBLY.

    Shared by every public board so the rule has one definition. The boards
    truncated silently at 14 while `app/routes/leaderboard.js` admits handles up
    to 20, which produced three distinct failures from one root cause:

    * ``BuddyKingTraderX`` and ``BuddyKingTraderY`` both rendered as
      ``BuddyKingTrade`` — two verified members, indistinguishable, on a board
      whose entire point is verified identity. Same harm as repairing
      ``night$jar`` into ``nightjar``, arriving through the column width.
    * a member whose handle was longer than the column never matched the
      "this is you" marker, so they were invisible to themselves in exactly the
      list they came to find themselves in.
    * a member whose handle was EXACTLY the column width and was another
      member's prefix marked BOTH rows — publishing somebody else's record as
      theirs.

    The ellipsis does not make two long handles distinguishable; nothing at this
    width can. It makes them visibly INCOMPLETE, which is the honest claim — a
    shortened name a reader knows is shortened, rather than a whole name that is
    not. Identity comparisons must use the full handle, never this.
    """
    text = str(raw or "")
    if width <= 0:
        return ""
    return text if len(text) <= width else text[: width - 1] + "…"


def valid_ref_code(code: Any) -> bool:
    """Whether a referral code is safe to put in a deep link."""
    return bool(code) and bool(_REF_CODE_RE.match(str(code)))


def invite_link(bot_username: Optional[str],
                ref_code: Optional[str] = None) -> Optional[str]:
    """`https://t.me/<bot>?start=ref_<code>`, or None when unbuildable.

    None, not a guessed username. The bot's own handle is not in its config
    (only the website's Login Widget carries TELEGRAM_BOT_USERNAME), so the
    caller supplies it — from `get_me()` at startup, ideally, which cannot be
    wrong. A hardcoded fallback would produce a link that looks right and sends
    people to somebody else's bot after a rename.
    """
    handle = str(bot_username or "").strip().lstrip("@")
    if not handle:
        return None
    base = f"https://t.me/{handle}"
    if ref_code and valid_ref_code(ref_code):
        return f"{base}?start={REF_PREFIX}{ref_code}"
    return base


def parse_start_payload(payload: Any) -> Optional[str]:
    """The referral code out of a `/start ref_<code>` argument, or None.

    `/start` ignored its payload entirely, so every invite link the website can
    mint was inert. Returns None for anything that is not a well-formed ref
    payload — an unknown deep link is not a referral and must not be recorded
    as one.
    """
    raw = str(payload or "").strip()
    if not raw.startswith(REF_PREFIX):
        return None
    code = raw[len(REF_PREFIX):]
    return code if valid_ref_code(code) else None


def _pct(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def close_share_text(close_data: Optional[Mapping[str, Any]],
                     invite: Optional[str] = None) -> Optional[str]:
    """A closed trade told in percent, or None when it cannot be told honestly.

    None rather than a partial card: a share with no number is an empty brag,
    and a share with an invented one is worse. The caller omits the button.

    Reports the MARGIN return (what the operator's own card shows, so the two
    agree) and qualifies it with the leverage and the underlying price move
    whenever those are known — see the module docstring on why an unqualified
    leveraged percent misleads even though it breaks no dollar rule.
    """
    if not close_data:
        return None
    symbol = str(close_data.get("symbol") or "").replace("/", "").replace(":USDT", "")
    direction = str(close_data.get("direction") or "").upper()
    if not symbol or direction not in ("LONG", "SHORT"):
        return None

    margin_pct = _pct(close_data.get("pnl_pct_margin"))
    notional_pct = _pct(close_data.get("pnl_pct"))
    headline = margin_pct if margin_pct is not None else notional_pct
    if headline is None:
        return None      # no readable percent — nothing honest to publish

    arrow = "📈" if headline >= 0 else "📉"
    lines = [f"{arrow} {symbol} {direction} closed {headline:+.2f}%"]

    leverage = _pct(close_data.get("leverage"))
    if margin_pct is not None and notional_pct is not None and leverage and leverage > 1:
        lines.append(f"({leverage:.0f}× on a {notional_pct:+.2f}% move)")
    hold = str(close_data.get("hold_time") or "").strip()
    if hold:
        lines.append(f"held {hold}")
    lines.append("— RUNECLAW")
    if invite:
        lines.append(invite)
    return assert_no_money("\n".join(lines))


def telegram_share_url(text: str, link: Optional[str] = None) -> str:
    """Telegram's share sheet, pre-filled. Opens the chat picker on tap.

    `url` and `text` are both sent: Telegram shows the link as the shared
    object and the text as its caption, so a share that lands in a group
    carries the invite whether or not the reader expands anything.
    """
    assert_no_money(text)
    params = f"url={quote(link or '', safe='')}&text={quote(text, safe='')}"
    return f"{_SHARE_BASE}?{params}"


def close_share_button(close_data: Optional[Mapping[str, Any]],
                       bot_username: Optional[str] = None,
                       ref_code: Optional[str] = None) -> Optional[dict]:
    """`{"text": ..., "url": ...}` for an inline keyboard, or None.

    Returned as a plain dict so the decision — what may be shared, and whether
    anything may be — is testable without constructing a PTB keyboard or a
    Telegram client.
    """
    link = invite_link(bot_username, ref_code)
    text = close_share_text(close_data, link)
    if not text:
        return None
    return {"text": "📣 Share this win" if _is_win(close_data) else "📣 Share",
            "url": telegram_share_url(text, link)}


def _is_win(close_data: Optional[Mapping[str, Any]]) -> bool:
    """True only for a POSITIVE readable return.

    `>= 0` would label an unreadable close a win — the `(x or 0) >= 0` shape
    this repo has been bitten by. Unknown is not a win.
    """
    if not close_data:
        return False
    value = _pct(close_data.get("pnl_pct_margin"))
    if value is None:
        value = _pct(close_data.get("pnl_pct"))
    return value is not None and value > 0
