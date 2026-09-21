"""The balance-relative bounds' RECORD: what they did, and would have done.

WHY. `SIZE_BOUNDS_ENABLED` ships OFF, and with it off `size_bounds.resolve`
answers ``flat`` -- the operator's constants -- and computes no would-be,
although the AVAILABLE balance the venue reported is in hand on every live
order regardless (`LiveExecutor.execute` reads it once and hands it to the
clamp and the preflight). So the evidence the operator needs before arming
the flag -- how often the balance-relative bound would have been tighter than
the flat $100, and on how many orders it would have cut or refused -- was
computable for free and recorded nowhere. The quality ladder had the same
shape one flag over (`bot/risk/ladder_shadow.py`), and this is that fix
applied to the bounds.

WHAT IS RECORDED. One row per live order the preflight was asked about, on
every account this bot executes for (the executor knows its account, so the
card counts per account; the ladder's record names the ENGINE that evaluated,
which is the same fact one gate up). The row carries the
order as the preflight saw it (already clamped to the bounds IN FORCE), the
balance read (or that it was not), the bounds in force and the bounds that
WOULD be in force with the flag on -- `size_bounds.resolve` over the same
balance with ``enabled=True`` -- and the verdict each set gives, from the ONE
reading the preflight itself uses (`size_bounds.bounds_verdict`). A second
copy of the comparison would be a second answer about what refuses an order.

WHAT IS NOT CLAIMED. The would-be per-trade bound is a CLAMP (the executor
cuts the size to it before the preflight), so the row says "would have cut to
$X" and evaluates the total and the reserve at THAT size. With the flag on,
the executor hands the size it saw BEFORE the clamp too, so an applied cut is
a reading rather than an inference; a caller that hands none leaves the
applied count unmeasured, and the card says so. Paper fills never reach the
executor: the population is LIVE orders, and the card says that first. No
recommendation is derived from a count.
"""

from __future__ import annotations

import html
import os
from collections import Counter
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

from bot.core import size_bounds
from bot.utils.shadow_ledger import STATE_UNREADABLE, ShadowLedger, stamp

_STATE_DIR = os.environ.get("RUNECLAW_STATE_DIR", "data")
DEFAULT_STATE_FILE = os.path.join(_STATE_DIR, "bounds_ledger.json")

OPERATOR = "operator"

ROW_KEYS = ("ts", "account", "symbol", "size_usd", "size_before_usd", "available_usd",
            "enabled", "in_force_basis", "in_force_per_trade", "in_force_total",
            "in_force_state", "would_basis", "would_per_trade", "would_total",
            "would_reserve", "would_size_usd", "would_state", "would_reserve_state",
            "exposure_total", "exposure_scored", "exposure_counted")


def _account_word(user_id: Any) -> str:
    return OPERATOR if user_id is None else str(user_id)


def order_row(*, account: Any, symbol: str, size_usd: float,
              available_usd: Optional[float], in_force: size_bounds.SizeBounds,
              would: size_bounds.SizeBounds, exposure: Any, enabled: bool,
              size_before_usd: Optional[float] = None,
              now: Optional[float] = None) -> Dict[str, Any]:
    """One preflighted order, as the ledger records it. Pure.

    ``size_usd`` is the order as the preflight saw it -- clamped to the bounds
    in force. ``would_size_usd`` is that figure clamped again to the would-be
    per-trade bound (a no-op unless the would-be bound is tighter), and the
    would-be total and reserve are read at THAT size, because that is the
    order the flag would have placed.
    """
    import time
    size = float(size_usd)
    would_size = min(size, float(would.per_trade_usd))
    in_v = size_bounds.bounds_verdict(size, in_force, exposure)
    w_v = size_bounds.bounds_verdict(would_size, would, exposure)
    w_reserve: Optional[str] = None
    if w_v.state == "ok" and w_v.exposure_total is not None:
        w_reserve = size_bounds.reserve_read(would_size, would, w_v.exposure_total).state
    return {
        "ts": float(time.time() if now is None else now),
        "account": _account_word(account),
        "symbol": str(symbol or ""),
        "size_usd": round(size, 2),
        "size_before_usd": (None if size_before_usd is None else round(float(size_before_usd), 2)),
        "available_usd": (None if available_usd is None else float(available_usd)),
        "enabled": bool(enabled),
        "in_force_basis": in_force.basis,
        "in_force_per_trade": float(in_force.per_trade_usd),
        "in_force_total": float(in_force.total_usd),
        "in_force_state": in_v.state,
        "would_basis": would.basis,
        "would_per_trade": float(would.per_trade_usd),
        "would_total": float(would.total_usd),
        "would_reserve": (None if would.reserve_usd is None else float(would.reserve_usd)),
        "would_size_usd": round(would_size, 2),
        "would_state": w_v.state,
        "would_reserve_state": w_reserve,
        "exposure_total": (None if exposure.total is None else float(exposure.total)),
        "exposure_scored": int(exposure.scored),
        "exposure_counted": int(exposure.counted),
    }


BOUNDS_LEDGER = ShadowLedger(DEFAULT_STATE_FILE)


class BoundsSummary(NamedTuple):
    n: int                         # readable rows
    unreadable: int                # rows another build wrote
    first_ts: Optional[float]
    last_ts: Optional[float]
    accounts: Tuple[Tuple[str, int], ...]   # (account, rows), most rows first
    balance_read: int              # rows whose would-be bounds came from a read balance
    balance_unread: int            # rows bounded by the flat figures under either flag
    would_cut: int                 # balance rows the would-be per-trade bound would have cut
    would_cut_from: float          # sum of size_usd over those rows
    would_cut_to: float            # sum of would_size_usd over those rows
    would_wider: int               # balance rows whose would-be per-trade bound is ABOVE the one in force
    would_refused_total: int       # balance rows the would-be total bound would have refused
    would_reserve_warn: int        # balance rows the would-be reserve would have warned on
    in_force_refused: int          # rows the bounds in force refused (per-trade or total)
    exposure_unmeasured: int       # rows where the total could not be enforced under either
    applied_rows: int              # rows recorded with the flag on
    applied_cut: int               # of those, orders the clamp cut (needs size_before_usd)
    applied_unmeasured: int        # of those, rows with no size_before_usd (the clamp could not be seen)


def _readable(row: Dict[str, Any]) -> bool:
    return all(k in row for k in ROW_KEYS)


def _figure(value: Any) -> Optional[float]:
    """A figure a row carries, or None for one it does not (absent or junk).
    Never a zero: a row that says nothing about a size is not a row that
    says the size was zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def summarize(rows: Sequence[Dict[str, Any]]) -> BoundsSummary:
    readable = [r for r in rows if _readable(r)]
    unreadable = len(rows) - len(readable)
    tss: List[float] = []
    accounts: Counter = Counter()
    read = unread = cut = wider = refused_total = warn = in_ref = unmeasured = 0
    cut_from = cut_to = 0.0
    applied = applied_cut = applied_unm = 0
    for r in readable:
        try:
            tss.append(float(r["ts"]))
        except (TypeError, ValueError):
            pass
        accounts[str(r["account"])] += 1
        if r["in_force_state"] in ("per_trade", "total"):
            in_ref += 1
        if r["in_force_state"] in ("exposure_unread", "exposure_partial"):
            unmeasured += 1
        if r["enabled"]:
            applied += 1
            # One reading of both figures. The first draft tested `before is
            # None` and then let `float(before)` raise into an except that
            # counted the same bucket -- so the None test was a branch no
            # input could reach differently, and the mutation round said so
            # (an EQUIVALENT mutant is the code claiming a check it does not
            # make). An unreadable figure on either side is one unmeasured
            # row, decided once.
            before = _figure(r["size_before_usd"])
            placed = _figure(r["size_usd"])
            if before is None or placed is None:
                applied_unm += 1
            elif before > placed:
                applied_cut += 1
        if r["would_basis"] != "balance":
            unread += 1
            continue
        read += 1
        try:
            size, wsize = float(r["size_usd"]), float(r["would_size_usd"])
            if wsize < size:
                cut += 1
                cut_from += size
                cut_to += wsize
            if float(r["would_per_trade"]) > float(r["in_force_per_trade"]):
                wider += 1
        except (TypeError, ValueError):
            pass
        if r["would_state"] == "total":
            refused_total += 1
        if r["would_reserve_state"] == "warn":
            warn += 1
    return BoundsSummary(len(readable), unreadable,
                         min(tss) if tss else None, max(tss) if tss else None,
                         tuple(accounts.most_common()), read, unread, cut, cut_from, cut_to,
                         wider, refused_total, warn, in_ref, unmeasured,
                         applied, applied_cut, applied_unm)


# ── the card ────────────────────────────────────────────────────────────────

def _flag_word(on: bool) -> str:
    return "✅ ON" if on else "⬜ OFF"


def render_bounds_report(ledger: ShadowLedger, exec_cfg: Any, *,
                         flat_per_trade: float, flat_total: float) -> str:
    """Telegram-ready. Three states at the top -- the file could not be read,
    nothing preflighted on record, a record -- and every count with its span.
    ``flat_per_trade`` / ``flat_total`` are the executor's own constants, the
    figures the ceilings default to, handed in for the reason
    `size_bounds_for` gives."""
    on = bool(getattr(exec_cfg, "balance_relative_bounds_enabled", False))
    trade_pct = size_bounds._pct(getattr(exec_cfg, "balance_bounds_per_trade_pct", None), 10.0)
    total_pct = size_bounds._pct(getattr(exec_cfg, "balance_bounds_total_pct", None), 50.0)
    reserve_pct = size_bounds._pct(getattr(exec_cfg, "balance_bounds_reserve_pct", None), 20.0)
    ceil_trade = max(size_bounds._pos_float(
        getattr(exec_cfg, "balance_bounds_max_position_usd", None), flat_per_trade), flat_per_trade)
    ceil_total = max(size_bounds._pos_float(
        getattr(exec_cfg, "balance_bounds_max_total_usd", None), flat_total), flat_total)
    lines: List[str] = [
        "<b>Balance-relative bounds — what they would have done</b>",
        "─" * 16,
        f"Flag: SIZE_BOUNDS_ENABLED {_flag_word(on)} · per-trade {trade_pct:g}% of available · "
        f"total {total_pct:g}% · reserve {reserve_pct:g}%",
        f"Ceilings: ${ceil_trade:,.2f} per trade / ${ceil_total:,.2f} total"
        + (" (the flat caps — growth needs SIZE_BOUNDS_MAX_*)"
           if (ceil_trade == flat_per_trade and ceil_total == flat_total) else ""),
    ]
    rows = ledger.rows()
    if ledger.load_state == STATE_UNREADABLE:
        lines.append(f"⚠️ The record on disk (<code>{html.escape(ledger.state_file)}</code>) "
                     f"could not be read ({html.escape(ledger.load_detail or 'unreadable')}) "
                     f"and is not being overwritten. {ledger.recorded_since_load} live order(s) "
                     f"recorded in memory since {stamp(ledger.loaded_at)}.")
        if not rows:
            return "\n".join(lines)
    elif not rows:
        lines.append("No live order on record yet — nothing has reached the executor's "
                     "preflight since the record began, so there is nothing to read. "
                     "This is not a reading of the bounds.")
        return "\n".join(lines)
    s = summarize(rows)
    span = f"{stamp(s.first_ts)} → {stamp(s.last_ts)}" if s.n else "no readable row"
    who = " · ".join(f"{html.escape(a)} {n}" for a, n in s.accounts)
    lines.append(f"Record: {s.n} live order(s) preflighted · {span} · {who}")
    if s.unreadable:
        lines.append(f"  {s.unreadable} row(s) another build wrote could not be read and "
                     f"are counted here, not below")
    if s.balance_unread:
        lines.append(f"  balance unread on {s.balance_unread} of {s.n} — bounded by the flat "
                     f"figures under either flag, so nothing to compare")
    if s.balance_read:
        head = f"  balance read on {s.balance_read} of {s.n}:"
        parts: List[str] = []
        if s.would_cut:
            parts.append(f"the per-trade bound would have cut {s.would_cut} "
                         f"(avg ${s.would_cut_from / s.would_cut:,.2f} → "
                         f"${s.would_cut_to / s.would_cut:,.2f})")
        else:
            parts.append("the per-trade bound would have cut none")
        if s.would_wider:
            parts.append(f"it would have been WIDER than the bound in force on {s.would_wider} "
                         f"(a raised ceiling)")
        parts.append(f"the total bound would have refused {s.would_refused_total}")
        parts.append(f"the reserve would have warned on {s.would_reserve_warn}")
        lines.append(head + " " + " · ".join(parts))
    if s.in_force_refused:
        lines.append(f"  the bounds in force refused {s.in_force_refused} of {s.n}")
    if s.exposure_unmeasured:
        lines.append(f"  the total could not be enforced on {s.exposure_unmeasured} of {s.n} "
                     f"(margin unread on the book) — under either flag")
    if s.applied_rows:
        seen = s.applied_rows - s.applied_unmeasured
        line = (f"  with the flag on: {s.applied_rows} of {s.n} were placed under the "
                f"balance-relative bounds; the clamp cut {s.applied_cut} of the {seen} "
                f"whose pre-clamp size was recorded")
        if s.applied_unmeasured:
            line += f" ({s.applied_unmeasured} recorded no pre-clamp size)"
        lines.append(line)
    foot = ["Live orders only — a paper fill never reaches the executor, and a refusal "
            "before the preflight leaves no row. The would-be bound is a clamp: the "
            "total and the reserve are read at the size it would have placed."]
    if ledger.full:
        foot.append(f"The record keeps the last {ledger.max_rows} rows and is full, so older "
                    f"rows may have been dropped.")
    lines.append(f"<i>{' '.join(foot)}</i>")
    return "\n".join(lines)
