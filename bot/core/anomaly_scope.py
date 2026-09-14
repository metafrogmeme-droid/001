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

# ── The third dial: how many ADVISORY messages an hour the channel may carry ──
#
# THE SECOND REPORT, 2026-09-14: "Still we have 20+ more anomaly messages last
# hour this is to much." The scope and interval above govern ONE of the
# monitor's thirty check paths (`_check_black_swan`, at its entry). The other
# twenty-nine share only `DEDUP_COOLDOWN`, and that is applied PER KEY — and
# thirteen of the twenty `dedup_key=` sites mint a key per symbol or per trade
# (`sl_prox_{asset}_{trade_id}`, `slippage_high_{symbol}`, `unprotected_{tid}`
# …). So the ceiling is twelve messages an hour PER KEY, and the key count is
# the position count: four positions hovering near their stops is ninety-six
# an hour with every per-key rule satisfied. Nothing bounded the CHANNEL.
#
# A blanket cap would be wrong, and this module's own opening says why: it
# "only trades a real warning for a quieter flood of irrelevant ones" — twelve
# SL-proximity messages would spend the allowance and a black-swan card would
# be the one dropped. So the budget is SEVERITY-AWARE: CRITICAL is never
# budgeted, because those are the alerts that name the reader's own risk
# (a tripped breaker, an unprotected position, a time-stop close, the brain
# offline). WARNING and INFO are advisory by their own definition and share
# the hour's allowance.
#
# Twelve, not twenty: the operator called twenty too many, and one advisory
# message every five minutes on average is the per-key ceiling made channel
# wide. It is a dial, and raising it is one command.
#
# WHAT IS HELD BACK IS SAID. The next message that goes through carries the
# count — the same rule `scoped()` follows for symbols it removed, and
# `_anomaly_digest`'s footer for severity: a quiet channel that does not say
# why it is quiet is a claim that the market is quiet.
DEFAULT_BUDGET_PER_HOUR = 12
MIN_BUDGET_PER_HOUR = 1
MAX_BUDGET_PER_HOUR = 500
BUDGET_WINDOW_SEC = 3600.0


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


def normalise_budget(value: Any) -> Optional[int]:
    """Advisory messages per hour, or None when the value is not a usable one.

    Same refusal shape as `normalise_interval`: junk is not defaulted, because
    a corrupt stored row must not be able to widen the channel.
    """
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return None
    if n < MIN_BUDGET_PER_HOUR or n > MAX_BUDGET_PER_HOUR:
        return None
    return n


def is_budgeted(severity: Any) -> bool:
    """Whether an alert of this severity draws on the hourly channel budget.

    CRITICAL never does. Anything else does — INCLUDING a severity nobody
    recognises. An unknown word is not a reason to bypass the one control
    that bounds the channel; the safe direction for "not a known priority" is
    "not a priority".
    """
    return str(severity or "").strip().upper() != "CRITICAL"


def channel_budget_allows(sent_at: list, now: float, per_hour: int,
                          window: float = BUDGET_WINDOW_SEC) -> tuple:
    """``(allowed, fresh)`` — may one more advisory go out, and the pruned
    send-time list to persist.

    PURE AND MODULE-LEVEL for the reason `apply_hourly_budget` states on the
    black-swan card budget: a rate limiter is exactly the code whose
    interesting cases — budget exactly exhausted, window boundary, empty
    history — never occur in a manual test and never occur in production
    until the day they matter. ``now`` is a parameter so a test can pin the
    clock far from zero; `time.monotonic()` starts near zero on a fresh host,
    and "an hour ago" planted as ``monotonic() - 3600`` is NEGATIVE there.

    This only decides. Charging the budget is the sender's job, AFTER the
    send — a message that was built and then suppressed, or that failed to
    send, has not spoken to the operator and must not spend their allowance.
    """
    try:
        per_hour = int(per_hour)
    except (TypeError, ValueError):
        per_hour = DEFAULT_BUDGET_PER_HOUR
    fresh = [t for t in (sent_at or []) if now - float(t) < window]
    return len(fresh) < per_hour, fresh


def held_back_note(held: int, per_hour: int) -> str:
    """The sentence a message carries when the budget held others back.

    Empty when nothing was held: a note on every message is a note nobody
    reads. Otherwise it says the count, the dial, and that critical alerts
    were never subject to it — because "N alerts held back" alone reads as
    "you may have missed the important one".
    """
    if held <= 0:
        return ""
    plural = "alert" if held == 1 else "alerts"
    return (f"{held} advisory {plural} held back this hour by the "
            f"<code>/alerts budget</code> of {per_hour}/hour — critical "
            "alerts are never held")


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
    budget = int(prefs.get("budget", DEFAULT_BUDGET_PER_HOUR))
    lines = [
        "\U0001f514 <b>Anomaly alerts</b>",
        "\u2500" * 16,
        f"- Scope: <code>{scope}</code>"
        + ("  (only symbols you hold)" if scope == SCOPE_HELD
           else "  (every scanned symbol)"),
        f"- At most one message every <code>{_human_interval(interval)}</code>",
        f"- Advisory budget: <code>{budget}</code> an hour across every "
        "alert type (critical alerts are never held)",
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
        "<code>/alerts budget 20</code> — advisory messages per hour "
        f"({MIN_BUDGET_PER_HOUR}\u2013{MAX_BUDGET_PER_HOUR})",
    ]
    return "\n".join(lines)


def parse_setting(args) -> tuple[Optional[str], Optional[int], Optional[int], str]:
    """``(scope, interval, budget, error)`` for `/alerts <args>`.

    All four can be empty: no args is a READ, which is why this refuses
    rather than defaulting. An unparseable value is an error the operator
    sees, not a silent fallback — they asked for something specific and got
    something else is the shape this whole slice is about.
    """
    parts = [str(a).strip().lower() for a in (args or []) if str(a).strip()]
    if not parts:
        return None, None, None, ""
    head = parts[0]
    scope = normalise_scope(head)
    if scope:
        return scope, None, None, ""
    if head in ("budget", "max", "limit"):
        if len(parts) < 2:
            return None, None, None, ("say how many an hour — for example "
                                      "<code>/alerts budget 20</code>")
        got = normalise_budget(parts[1])
        if got is None:
            return None, None, None, (f"budget must be a whole number between "
                                      f"{MIN_BUDGET_PER_HOUR} and "
                                      f"{MAX_BUDGET_PER_HOUR}")
        return None, None, got, ""
    if head in ("every", "interval", "each"):
        if len(parts) < 2:
            return None, None, None, ("say how often — for example "
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
            return None, None, None, f"could not read <code>{parts[1]}</code> as a time"
        got = normalise_interval(secs)
        if got is None:
            return None, None, None, (f"interval must be between "
                                      f"{MIN_INTERVAL_SEC}s and "
                                      f"{MAX_INTERVAL_SEC // 3600}h")
        return None, got, None, ""
    return None, None, None, (f"did not understand <code>{head}</code> — "
                              "try <code>all</code>, <code>held</code>, "
                              "<code>every 2h</code> or <code>budget 20</code>")
