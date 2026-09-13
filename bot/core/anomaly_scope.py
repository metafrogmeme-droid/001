"""Which anomalies are THIS operator's business, and how often to say so.

THE REPORT. Four messages in fifteen minutes on 2026-09-13 — a "+1 more
severe anomaly" at 09:50, an ANOMALY DIGEST naming eleven symbols at 10:02, a
severe BNB/USDT spread card at 10:04, another "+1 more" at 10:05 — across
WLFI, LAB, PENDLE, PUMP, RAVE, UNI, ATOM, BCH, BNB and XPL. The operator held
none of them.

EVERY EXISTING CONTROL BOUNDS VOLUME AND NONE BOUNDS RELEVANCE.
`_SEVERE_CARDS_PER_TICK` caps how wide one burst is, `_SEVERE_CARDS_PER_HOUR`
caps how many bursts an hour holds, `BLACK_SWAN_SEVERE_REPEAT` caps how often
one unchanged condition repeats, and the digest batches the mild ones. All
four are about HOW MANY. The detector's `active_alerts` is the whole scanned
universe, and nothing between it and the operator ever asked whether the
symbol was one they had money in — so tuning any of those numbers down only
trades a real warning for a quieter flood of irrelevant ones.

TWO DIALS, BOTH THE OPERATOR'S:

  scope    — `held` (default) or `all`
  interval — seconds between anomaly messages, default one hour

`held` is the default because it is the honest one: an anomaly on an asset
you do not hold is market news, and this product has other surfaces for
market news. `all` stays available and stays a choice somebody makes.

UNREADABLE IS NOT EMPTY, and here that rule decides the whole design. If the
book cannot be read, `held_symbols` answers **None** — not an empty set — and
`scoped` then keeps every alert and says why. An unreadable book that silently
became "you hold nothing" would turn a broken position read into total silence
on the one surface whose job is to interrupt you, which is the most expensive
direction this mistake has. A GENUINELY empty book is a real reading and does
suppress: the difference is the whole point of the third value.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

#: The two scopes. `held` is the default; `all` is the old behaviour, kept
#: because an operator watching the market rather than a book may want it.
SCOPE_HELD = "held"
SCOPE_ALL = "all"
SCOPES = (SCOPE_HELD, SCOPE_ALL)

#: One message an hour, which is what was asked for. A floor rather than a
#: schedule: nothing is sent on a timer, and a quiet hour sends nothing.
DEFAULT_INTERVAL_SEC = 3600

#: Below this an "interval" is not a limit anybody asked for, and above it the
#: setting starts to mean "off" without saying so — which is a thing an
#: operator should have to type, not arrive at.
MIN_INTERVAL_SEC = 60
MAX_INTERVAL_SEC = 86_400


def normalise_interval(value: Any) -> Optional[int]:
    """Seconds, or None when the value is not a usable interval.

    None rather than a default, because a caller handing this junk has a bug
    and silently substituting an hour would hide it — the setting surface
    turns None into a refusal the operator can read.
    """
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return None
    if n < MIN_INTERVAL_SEC or n > MAX_INTERVAL_SEC:
        return None
    return n


def normalise_scope(value: Any) -> Optional[str]:
    """One of SCOPES, or None when it is not one of them."""
    s = str(value or "").strip().lower()
    return s if s in SCOPES else None


def held_symbols(engine: Any) -> Optional[set[str]]:
    """Symbols with an open position, or **None** when the book is unreadable.

    None is NOT an empty set and the two must never be collapsed: empty means
    "read it, you hold nothing" and None means "could not read it". The first
    may suppress an alert; the second must never.

    Reads the operator's own book, because that is whose chats these alerts
    go to (`_configured_operator_chats`). A per-user version belongs with
    per-user alerting, which does not exist yet.
    """
    try:
        ex = getattr(engine, "live_executor", None)
        if ex is None:
            return None
        positions = getattr(ex, "open_positions", None)
        if positions is None:
            return None
        out: set[str] = set()
        for p in positions:
            sym = getattr(p, "symbol", None) or getattr(p, "asset", None)
            if sym:
                out.add(str(sym).upper())
        return out
    except Exception:
        return None


def scoped(alerts: Iterable[Any], held: Optional[set[str]],
           scope: str = SCOPE_HELD) -> tuple[list, list, str]:
    """``(kept, dropped, note)`` — the alerts worth sending, and why.

    `note` is empty when nothing needs saying. It is NOT empty when alerts
    were dropped or when the book could not be read, because a quiet channel
    that does not say why it is quiet is exactly the claim the digest's own
    footer already refuses to make about severity.
    """
    items = list(alerts)
    if scope != SCOPE_HELD:
        return items, [], ""
    if held is None:
        # Fail LOUD. The book is the filter, and a filter that cannot be read
        # must not become a filter that drops everything.
        return items, [], ("position book unreadable — showing every symbol "
                           "until it can be read again")
    kept: list[Any] = []
    dropped: list[Any] = []
    for a in items:
        sym = str(getattr(a, "symbol", "") or "").upper()
        (kept if sym in held else dropped).append(a)
    if not dropped:
        return kept, [], ""
    if not held:
        return kept, dropped, (f"{len(dropped)} anomal"
                               f"{'y' if len(dropped) == 1 else 'ies'} on "
                               "symbols you do not hold — you have no open "
                               "positions right now")
    return kept, dropped, (f"{len(dropped)} anomal"
                           f"{'y' if len(dropped) == 1 else 'ies'} on symbols "
                           "you do not hold (say /alerts all to see them)")


#: NOT `due`. Two classes define a method by that name (`proofofpnl.scheduler`
#: and `core.self_audit`), and `_multiply_defined_methods` drops any shared
#: method name that is ALSO a module-level function — `len(s) > 1 and n not in
#: module_level`. So calling this `due` did not RESOLVE those two methods, it
#: stopped the sweep checking them at all, and the ambiguity count fell 31 -> 30
#: while coverage went DOWN. A number that improves for the wrong reason is the
#: quiet half of that ratchet; the cheap fix is not to collide.
def is_due(last_sent: Optional[float], now: float, interval: int) -> bool:
    """Whether an anomaly message may go out.

    A never-sent channel is due immediately: the first alert after a restart
    is the one most worth having, and making the operator wait an hour for it
    would be the cure doing the disease's job.
    """
    if last_sent is None:
        return True
    try:
        return (now - float(last_sent)) >= interval
    except (TypeError, ValueError):
        return True


def _human_interval(seconds: int) -> str:
    if seconds % 3600 == 0:
        n = seconds // 3600
        return f"{n} hour" + ("" if n == 1 else "s")
    if seconds % 60 == 0:
        n = seconds // 60
        return f"{n} minute" + ("" if n == 1 else "s")
    return f"{seconds}s"


def settings_card(prefs: dict, held: Optional[set[str]] = None) -> str:
    """What the operator's dials are set to, and what that means right now.

    It prints the BOOK as well as the setting, because "held only" is a
    promise about a list, and an operator who cannot see the list cannot tell
    a quiet channel from a broken one — the same reason the digest's footer
    refuses to let silence imply calm. An unreadable book says so; an empty
    one says so differently.
    """
    scope = prefs.get("scope", SCOPE_HELD)
    interval = int(prefs.get("interval", DEFAULT_INTERVAL_SEC))
    lines = [
        "\U0001f514 <b>Anomaly alerts</b>",
        "\u2500" * 16,
        f"- Scope: <code>{scope}</code>"
        + ("  (only symbols you hold)" if scope == SCOPE_HELD
           else "  (every scanned symbol)"),
        f"- At most one message every <code>{_human_interval(interval)}</code>",
    ]
    if scope == SCOPE_HELD:
        if held is None:
            lines.append("- Your open positions: <i>could not be read</i> — "
                         "until they can, every symbol is shown rather than "
                         "none")
        elif not held:
            lines.append("- Your open positions: <code>none</code> — so no "
                         "anomaly alerts will be sent while the book is flat")
        else:
            lines.append(f"- Watching <code>{len(held)}</code>: "
                         f"<code>{', '.join(sorted(held))}</code>")
    lines += [
        "\u2500" * 16,
        "<code>/alerts all</code> — every symbol the scanner sees",
        "<code>/alerts held</code> — only symbols you hold (default)",
        "<code>/alerts every 2h</code> — change the interval "
        f"({MIN_INTERVAL_SEC}s\u2013{MAX_INTERVAL_SEC // 3600}h)",
    ]
    return "\n".join(lines)


def parse_setting(args) -> tuple[Optional[str], Optional[int], str]:
    """``(scope, interval, error)`` for `/alerts <args>`.

    All three can be empty: no args is a READ, which is why this refuses
    rather than defaulting. An unparseable interval is an error the operator
    sees, not a silent fallback to an hour — they asked for something
    specific and got something else is the shape this whole slice is about.
    """
    parts = [str(a).strip().lower() for a in (args or []) if str(a).strip()]
    if not parts:
        return None, None, ""
    head = parts[0]
    scope = normalise_scope(head)
    if scope:
        return scope, None, ""
    if head in ("every", "interval", "each"):
        if len(parts) < 2:
            return None, None, ("say how often — for example "
                                "<code>/alerts every 2h</code>")
        raw = parts[1]
        mult = 1
        if raw.endswith("h"):
            mult, raw = 3600, raw[:-1]
        elif raw.endswith("m"):
            mult, raw = 60, raw[:-1]
        elif raw.endswith("s"):
            raw = raw[:-1]
        try:
            secs = int(float(raw)) * mult
        except (TypeError, ValueError):
            return None, None, f"could not read <code>{parts[1]}</code> as a time"
        got = normalise_interval(secs)
        if got is None:
            return None, None, (f"interval must be between "
                                f"{MIN_INTERVAL_SEC}s and "
                                f"{MAX_INTERVAL_SEC // 3600}h")
        return None, got, ""
    return None, None, (f"did not understand <code>{head}</code> — "
                        "try <code>all</code>, <code>held</code> or "
                        "<code>every 2h</code>")
