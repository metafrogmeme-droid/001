"""
Funding-arb pair PROPOSAL — both legs, sized over the caller's own venues,
priced against the fee bar, and placed by nothing.

The radar (bot.core.funding_radar) names the delta-neutral direction per
coin — long the venue where funding is lowest, short the venue where it is
highest — and the paper tracker (bot.core.arb_tracker) says whether that
carry has survived fees on the record. Neither knows anything about the
caller: which of the two venues they have linked, what each leg could be
sized to, or what the round trip would cost on THAT size. This module is
that reading, and it is a proposal rather than an order for the reason the
arb tracker's own header gives — evidence before capital. A card that could
be sized and priced against real balances is the last thing to build before
an execution path, and the first thing to read against the record.

Two rules decide most of the shapes here:

  * A leg's margin is SIX-valued, never a number-or-zero. ``read`` carries
    the venue's own equity figure; ``unpriced`` is a venue that answered with
    no figure in its currency; ``unreachable`` did not answer; ``not_linked``
    has no keys stored; ``unreadable`` has keys the store cannot decrypt;
    ``unavailable`` is a store that could not be asked at all. Each prints
    its own sentence, because "sized to $0" over a venue nobody could read
    is the failed-read-as-empty shape on the one card whose figures decide
    a real hedge.
  * Both legs carry the SAME notional at 1x — that is what delta-neutral
    means — so the size is the smaller of the two legs' equity, capped at
    what the caller asked for, and the card says which bound bit. A leg
    that could not be sized leaves the pair unsized; half a hedge is a
    naked position, and the card never proposes one.

Nothing here reads a private key: the venue read is `balance_snapshot`, the
same READ-ONLY fetch `/connect` validates with, and a snapshot is attempted
only for a venue the store says is readable — an unlinked venue is answered
without touching the network.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from bot.core.arb_tracker import (
    PAPER_NOTIONAL_USD,
    ROUND_TRIP_FEE_PCT,
    ArbVerdict,
)
from bot.core.exchange_credentials import balance_snapshot
from bot.core.funding_radar import FundingRow

HOURS_PER_YEAR = 24 * 365

#: The six things a leg's margin read can come back as. Only ``read`` carries
#: a figure, and a figure of 0.0 is a READ empty account, not an absence.
MARGIN_STATES = ("read", "unpriced", "unreachable", "not_linked", "unreadable", "unavailable")

_BASE_RE = re.compile(r"^[A-Z0-9]{2,10}$")
_MAX_REQUESTED_USD = 10_000_000.0


@dataclass(frozen=True)
class LegMargin:
    """One leg's sizing basis: the venue, the state word, and — for ``read``
    only — the equity the venue reported, in its settlement currency.
    ``detail`` is the venue's own reason on an ``unreachable`` read, kept
    for the log and never printed on the card."""
    venue: str
    state: str
    equity_usd: Optional[float] = None
    currency: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        if self.state not in MARGIN_STATES:
            raise ValueError(f"unknown margin state {self.state!r}")
        if (self.state == "read") != (self.equity_usd is not None):
            raise ValueError("a read carries a figure and nothing else does")


def leg_credentials(store: Any, uid: str, venue: str) -> tuple[str, Optional[dict]]:
    """``("readable", fields)`` for a venue the store can decrypt, else
    ``("not_linked", None)`` or ``("unreadable", None)``. Raises when the
    store cannot be asked — the caller files that as ``unavailable``."""
    if store is None:
        raise RuntimeError("credential store unavailable")
    states = store.venue_states(uid) or {}
    state = states.get(venue)
    if state is None:
        return "not_linked", None
    if state != "readable":
        return "unreadable", None
    fields = store.get_for_venue(uid, venue)
    if not fields:
        return "unreadable", None
    return "readable", fields


async def read_leg_margin(venue: str, uid: str, store: Any, *,
                          snapshot: Optional[Callable[..., Awaitable[dict]]] = None) -> LegMargin:
    """The caller's sizing basis on ONE venue, six-valued.

    A snapshot is attempted only for a venue whose keys the store produced;
    the network is never touched for an unlinked or undecryptable venue.
    `balance_snapshot` never raises by contract and its ``ok`` reports
    AUTHENTICATION, not the balance — an authenticated answer with no figure
    is ``unpriced``, which is not an empty account.
    """
    try:
        state, fields = leg_credentials(store, uid, venue)
    except Exception as exc:
        return LegMargin(venue, "unavailable", detail=type(exc).__name__)
    if state != "readable":
        return LegMargin(venue, state)
    assert fields is not None  # `readable` is the one answer that carries them
    # Resolved at CALL time, not bound as a default: a default argument
    # captures the function object at definition, so a test planting the
    # module's `balance_snapshot` would drive the real venue read.
    snap_fn = snapshot if snapshot is not None else balance_snapshot
    try:
        snap = await snap_fn(venue, fields)
    except Exception as exc:  # belt and braces: the seam's contract is never-raise
        return LegMargin(venue, "unreachable", detail=type(exc).__name__)
    if not isinstance(snap, dict) or not snap.get("ok"):
        detail = str((snap or {}).get("detail") or "") if isinstance(snap, dict) else ""
        return LegMargin(venue, "unreachable", detail=detail)
    currency = str(snap.get("currency") or "")
    eq = snap.get("equity_usd")
    if isinstance(eq, bool) or not isinstance(eq, (int, float)) or not math.isfinite(float(eq)):
        return LegMargin(venue, "unpriced", currency=currency)
    return LegMargin(venue, "read", equity_usd=float(eq), currency=currency)


def parse_pair_args(args: list) -> "tuple[str, float] | str":
    """``(BASE, requested_usd)`` from the command's words, or the sentence
    that says what was wrong with them. The base is a bare coin — "btc",
    "BTC/USDT", "btcusdt" all read as BTC — and is checked against a
    strict shape before it can reach a card, because the card prints it."""
    if not args:
        return ("Usage: /arbpair BTC [usd] — the coin, and optionally the notional "
                f"per leg in USD (default ${PAPER_NOTIONAL_USD:,.0f}).")
    raw = str(args[0]).strip().upper()
    base = raw
    for suffix in ("/USDT:USDT", "/USDT", ":USDT", "USDT", "/USDC", "USDC"):
        if base.endswith(suffix) and len(base) > len(suffix):
            base = base[: -len(suffix)]
            break
    base = base.strip(":/")
    if not _BASE_RE.match(base):
        return "That is not a base coin — try /arbpair BTC."
    requested = PAPER_NOTIONAL_USD
    if len(args) > 1:
        token = str(args[1]).strip().lstrip("$").replace(",", "")
        try:
            requested = float(token)
        except ValueError:
            return "The size has to be a dollar figure per leg — try /arbpair BTC 500."
        if not math.isfinite(requested) or requested <= 0 or requested > _MAX_REQUESTED_USD:
            return "The size has to be a positive dollar figure per leg — try /arbpair BTC 500."
    return base, requested


def breakeven_hours(spread_apr: float, fee_pct: float = ROUND_TRIP_FEE_PCT) -> Optional[float]:
    """Hours of carry at ``spread_apr`` that pay back one round trip, or None
    when the spread is not positive — a pair on a flat spread never breaks
    even, and "0 hours" would read as the opposite."""
    if spread_apr is None or not math.isfinite(spread_apr) or spread_apr <= 0:
        return None
    return fee_pct / spread_apr * HOURS_PER_YEAR


@dataclass(frozen=True)
class PairProposal:
    """The two legs and what they would cost and collect on the caller's
    accounts. ``notional_usd`` is None when a leg could not be sized —
    never zero, because a zero-sized hedge is a different statement (a
    READ empty account) and gets its own sentence."""
    base: str
    long_venue: str
    short_venue: str
    long_apr: float
    short_apr: float
    spread_apr: float
    requested_usd: float
    long_margin: LegMargin
    short_margin: LegMargin
    fee_pct: float = ROUND_TRIP_FEE_PCT
    notional_usd: Optional[float] = None
    sized_by: str = ""              # "requested" | the venue whose equity bound the size | ""

    @property
    def placeable(self) -> bool:
        return (self.notional_usd is not None and self.notional_usd > 0
                and self.spread_apr > 0)

    @property
    def fee_usd(self) -> Optional[float]:
        if self.notional_usd is None:
            return None
        return self.notional_usd * self.fee_pct / 100.0

    @property
    def breakeven_hours(self) -> Optional[float]:
        return breakeven_hours(self.spread_apr, self.fee_pct)

    @property
    def carry_per_day_usd(self) -> Optional[float]:
        if self.notional_usd is None or self.spread_apr <= 0:
            return None
        return self.notional_usd * (self.spread_apr / 100.0) / 365.0

    def blockers(self) -> tuple[str, ...]:
        """Why the pair cannot be placed as proposed — one reason per leg
        that could not be sized, plus a flat spread. Empty when placeable."""
        out = []
        for leg in (self.long_margin, self.short_margin):
            if leg.state != "read":
                out.append(f"{leg.venue} {_LEG_WORDS[leg.state]}")
            elif leg.equity_usd is not None and leg.equity_usd <= 0:
                out.append(f"{leg.venue} equity reads ${leg.equity_usd:,.2f}")
        if self.spread_apr <= 0:
            out.append("no positive spread to collect")
        return tuple(out)


_LEG_WORDS = {
    "not_linked": "is not linked",
    "unreadable": "has keys stored that could not be decrypted",
    "unreachable": "did not answer the balance read",
    "unpriced": "answered with no readable balance in its currency",
    "unavailable": "could not be asked — the credential store did not answer",
}


def propose_pair(row: FundingRow, long_margin: LegMargin, short_margin: LegMargin, *,
                 requested_usd: float = PAPER_NOTIONAL_USD,
                 fee_pct: float = ROUND_TRIP_FEE_PCT) -> PairProposal:
    """Size the pair the radar's row names over the two legs' margin reads.

    Pure: the row is the radar's, the margins are `read_leg_margin`'s, and
    the answer is what the card prints. Both legs get one notional — the
    smaller leg's equity, capped at the requested figure — and ``sized_by``
    names which bound decided it, so a caller who asked for $1,000 and is
    shown $300 is told why. A leg that is not ``read`` leaves the pair
    unsized; a leg READ at $0.00 does too, with the figure kept, because
    the two are different facts about the account.
    """
    if not math.isfinite(requested_usd) or requested_usd <= 0:
        raise ValueError("requested notional must be a positive finite figure")
    if long_margin.venue != row.long_venue or short_margin.venue != row.short_venue:
        raise ValueError("margin reads are not the row's legs")
    long_apr = float(row.rates[row.long_venue])
    short_apr = float(row.rates[row.short_venue])
    notional: Optional[float] = None
    sized_by = ""
    if long_margin.state == "read" and short_margin.state == "read":
        long_eq, short_eq = long_margin.equity_usd, short_margin.equity_usd
        assert long_eq is not None and short_eq is not None  # `read` carries a figure
        # The smaller leg bounds the pair; a tie names the long leg, which is
        # the one the caller reads first.
        smaller, cap = ((long_margin, long_eq) if long_eq <= short_eq
                        else (short_margin, short_eq))
        if cap > 0:
            if requested_usd <= cap:
                notional, sized_by = requested_usd, "requested"
            else:
                notional, sized_by = cap, smaller.venue
    return PairProposal(
        base=row.base, long_venue=row.long_venue, short_venue=row.short_venue,
        long_apr=long_apr, short_apr=short_apr, spread_apr=float(row.spread_apr),
        requested_usd=float(requested_usd), long_margin=long_margin,
        short_margin=short_margin, fee_pct=fee_pct, notional_usd=notional,
        sized_by=sized_by)


# ── the card ────────────────────────────────────────────────────────────────

PLACES_NOTHING = ("This build places no pair orders — the card sizes and prices the "
                  "hedge against your real balances so the record can be read against "
                  "them. Nothing was placed, nothing is armed, and no order follows "
                  "from this message.")

_VERDICT_ICON = {"survives": "🟢", "does_not": "🔴", "thin": "🟡", "unread": "🔴"}


def _leg_line(m: LegMargin) -> str:
    if m.state == "read":
        assert m.equity_usd is not None
        cur = f" {m.currency}" if m.currency else ""
        return (f"• <b>{m.venue}</b>: equity <code>${m.equity_usd:,.2f}</code>{cur} "
                "(total, as the venue reports it)")
    if m.state == "not_linked":
        return f"• <b>{m.venue}</b>: not linked — <code>/connect {m.venue}</code> links it"
    if m.state == "unreadable":
        return (f"• <b>{m.venue}</b>: keys stored but could not be decrypted — "
                f"<code>/exchange</code> shows the key state")
    if m.state == "unreachable":
        return f"• <b>{m.venue}</b>: did not answer the balance read — not an empty account"
    if m.state == "unpriced":
        cur = m.currency or "its settlement currency"
        return f"• <b>{m.venue}</b>: authenticated, but no readable {cur} balance in its answer"
    return f"• <b>{m.venue}</b>: could not be asked — the credential store did not answer"


def _hours_words(h: float) -> str:
    if h >= 48:
        return f"{h / 24:.1f} days ({h:,.0f} h)"
    return f"{h:.1f} h"


def format_pair_card(p: PairProposal, verdict: ArbVerdict) -> str:
    """Telegram HTML for the proposal, beside the record's verdict.

    Every figure on it is either read (a venue's equity), derived from reads
    (size, fee, carry, break-even), or absent with its reason. The size line
    says which bound decided it; the legs each carry their own sentence; the
    evidence line is the paper record's own verdict, never recomputed here;
    and the card ends by saying what this message did — nothing.
    """
    lines = [f"🔀 <b>Funding pair — {p.base}</b> (proposal · places nothing)",
             "━" * 30,
             f"Long  <b>{p.long_venue}</b> · funding <code>{p.long_apr:+.1f}%/yr</code>",
             f"Short <b>{p.short_venue}</b> · funding <code>{p.short_apr:+.1f}%/yr</code>",
             f"Spread <code>{p.spread_apr:.1f}%/yr</code> — what the pair collects before "
             "fees and slippage (positive funding = longs pay shorts)",
             ""]
    if p.notional_usd is not None:
        if p.sized_by == "requested":
            how = "as requested"
        else:
            how = (f"sized to {p.sized_by}'s equity — you asked for "
                   f"${p.requested_usd:,.2f}")
        lines.append(f"Size: <code>${p.notional_usd:,.2f}</code> per leg at 1x ({how})")
        fee = p.fee_usd
        assert fee is not None  # sized, so priced
        be = p.breakeven_hours
        be_words = (f"break-even hold ≈ <b>{_hours_words(be)}</b> at today's spread"
                    if be is not None else "<b>never</b> at today's spread — it is not positive")
        lines.append(f"Fee bar: <code>{p.fee_pct:.2f}%</code> round trip = "
                     f"<code>${fee:,.2f}</code> on this size — {be_words}")
        cpd = p.carry_per_day_usd
        if cpd is not None:
            lines.append(f"Carry at today's spread ≈ <code>${cpd:,.2f}/day</code>")
    else:
        lines.append(f"Size: unsized — you asked for <code>${p.requested_usd:,.2f}</code> per "
                     "leg, and a leg below could not be sized")
        be = p.breakeven_hours
        lines.append(f"Fee bar: <code>{p.fee_pct:.2f}%</code> of the notional, round trip — "
                     + (f"break-even hold ≈ <b>{_hours_words(be)}</b> at today's spread"
                        if be is not None else
                        "break-even <b>never</b> at today's spread — it is not positive"))
    lines += ["", "Legs on your accounts:", _leg_line(p.long_margin), _leg_line(p.short_margin), ""]
    icon = _VERDICT_ICON.get(verdict.state, "🟡")
    lines.append(f"Evidence (the paper record, /arb): {icon} <b>{verdict.reason}</b>")
    lines.append("")
    blockers = p.blockers()
    if p.placeable:
        lines.append(f"✅ Could be placed at <code>${p.notional_usd:,.2f}</code> per leg on "
                     "these two accounts.")
    else:
        lines.append("⛔ Cannot be placed as proposed: " + "; ".join(blockers) + ".")
    lines.append(f"<i>{PLACES_NOTHING}</i>")
    return "\n".join(lines)


def no_pair_card(base: str) -> str:
    """The card for a coin the radar could not pair: quoted on fewer than two
    of its venues, or the venues did not answer — the radar cannot say which,
    so neither does this."""
    return (f"🟡 <b>Funding pair — {base}</b>\n"
            f"No cross-venue spread to hedge: {base} was quoted on fewer than two of "
            "the radar's venues (Bitget, Bybit, Hyperliquid), or they did not answer. "
            "Nothing was placed.")
