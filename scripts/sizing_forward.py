#!/usr/bin/env python3
"""Equal-size positions against the bot's own sizing, forward, as sealed.

Design and rule: docs/research/prereg/equal_sizing_forward_2026-10/PREREGISTRATION.md.

    python3 scripts/sizing_forward.py status     # counts only, any time
    python3 scripts/sizing_forward.py evaluate   # once, after the window closes
    python3 scripts/sizing_forward.py status --book paper   # Addendum 1
    python3 scripts/sizing_forward.py status --book paper-limits   # Addendum 2

Reads the operator's live closes (``state_path("data/closed_trades.json")``,
or ``--trades``). With ``--book paper`` (Addendum 1, sealed 2026-10-31) it reads
the operator's paper book instead, ``data/portfolio_<operator>.json``'s
``history``, over its own window. With ``--book paper-limits`` (Addendum 2,
sealed 2026-11-08) it reads the same book over twelve weeks from the first
paper confirm made under the resting-limit fill model
(``data/paper_fill_model.json``, written by the bot), scoring only trades
opened at or after it. It changes nothing in the bot.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UTC = timezone.utc
START = datetime(2026, 10, 31, tzinfo=UTC)
END = datetime(2027, 1, 23, tzinfo=UTC)          # exclusive
MIN_SCORED = 40
# Addendum 1: the paper book, over a window that opens after the addendum's seal.
PAPER_START = datetime(2026, 11, 1, tzinfo=UTC)
PAPER_END = datetime(2027, 1, 24, tzinfo=UTC)    # exclusive
# Addendum 2: the paper book under the resting-limit fill model, twelve weeks
# from the first paper confirm made under it.
FILL_MODEL_MARKER = "data/paper_fill_model.json"
FILL_MODEL = "rests_like_live"
PAPER_LIMITS_WEEKS = 12
#: The net field each book writes: the live executor's, and the paper tracker's.
LIVE_NET_KEYS = ("pnl_usd", "net_pnl", "net_pnl_usd")
PAPER_NET_KEYS = ("pnl",)
#: `PortfolioTracker` keeps at most this many closes; at the cap, older ones are gone.
PAPER_HISTORY_CAP = 1000
BOOT, SEED, LOWER_PCT = 10_000, 7, 2.5


def _number(v) -> Optional[float]:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return None


def _when(raw) -> Optional[datetime]:
    try:
        t = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def score(rows: list[dict], start: datetime = START, end: datetime = END,
          net_keys: tuple = LIVE_NET_KEYS, opened_from: Optional[datetime] = None,
          opened_before: Optional[datetime] = None) -> tuple[list[dict], dict]:
    """The window's scored closes ``{week, net, notional}`` and the count of
    rows left out, by reason. Nothing unreadable is scored as zero.

    With ``opened_from`` (Addendum 2) a close in the window is scored only
    when its ``opened_at`` reads and is at or after it; one opened before is
    counted apart, as filled under the old model. With ``opened_before``, the
    other half: only closes opened before it, the new model's counted apart."""
    scored, left_out = [], {"no_close_time": 0, "no_net": 0, "no_notional": 0}
    if opened_from is not None or opened_before is not None:
        left_out["no_open_time"] = 0
    if opened_from is not None:
        left_out["opened_under_the_old_fill_model"] = 0
    if opened_before is not None:
        left_out["opened_under_the_new_fill_model"] = 0
    for t in rows:
        when = _when(t.get("closed_at")) if t.get("closed_at") else None
        if when is None:
            left_out["no_close_time"] += 1
            continue
        if not (start <= when < end):
            continue
        if opened_from is not None or opened_before is not None:
            opened = _when(t.get("opened_at")) if t.get("opened_at") else None
            if opened is None:
                left_out["no_open_time"] += 1
                continue
            if opened_from is not None and opened < opened_from:
                left_out["opened_under_the_old_fill_model"] += 1
                continue
            if opened_before is not None and opened >= opened_before:
                left_out["opened_under_the_new_fill_model"] += 1
                continue
        net = next((x for x in (_number(t.get(k)) for k in net_keys)
                    if x is not None), None)
        entry, qty = _number(t.get("entry_price")), _number(t.get("quantity"))
        if net is None:
            left_out["no_net"] += 1
            continue
        if entry is None or qty is None or entry <= 0 or qty <= 0:
            left_out["no_notional"] += 1
            continue
        y, w, _ = when.isocalendar()
        scored.append({"week": (y, w), "net": net, "notional": abs(entry * qty)})
    return scored, left_out


def judge(scored: list[dict]) -> dict:
    """E (equal size), W (as sized), and both bootstrap lower bounds."""
    out: dict = {"n": len(scored), "weeks": len({s["week"] for s in scored}),
                 "E": None, "W": None, "E_lower": None, "diff_lower": None,
                 "q1_equal_size_makes_money": None, "q2_equal_beats_sized": None}
    if len(scored) < MIN_SCORED:
        out["verdict"] = f"no verdict: {len(scored)} scored closes, fewer than {MIN_SCORED}"
        return out
    r = np.array([s["net"] / s["notional"] for s in scored])
    net = np.array([s["net"] for s in scored])
    notional = np.array([s["notional"] for s in scored])
    out["E"], out["W"] = float(r.mean()), float(net.sum() / notional.sum())
    weeks = sorted({s["week"] for s in scored})
    idx = {w: i for i, w in enumerate(weeks)}
    wk = np.array([idx[s["week"]] for s in scored])
    r_sum, r_n = np.bincount(wk, r, len(weeks)), np.bincount(wk, None, len(weeks))
    net_sum, not_sum = np.bincount(wk, net, len(weeks)), np.bincount(wk, notional, len(weeks))
    draw = np.random.default_rng(SEED).integers(0, len(weeks), size=(BOOT, len(weeks)))
    e = r_sum[draw].sum(1) / r_n[draw].sum(1)
    w = net_sum[draw].sum(1) / not_sum[draw].sum(1)
    out["E_lower"] = float(np.percentile(e, LOWER_PCT))
    out["diff_lower"] = float(np.percentile(e - w, LOWER_PCT))
    out["q1_equal_size_makes_money"] = bool(out["E"] > 0 and out["E_lower"] > 0)
    out["q2_equal_beats_sized"] = bool(out["E"] - out["W"] > 0 and out["diff_lower"] > 0)
    out["verdict"] = "judged"
    return out


def load(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        data = data.get("closed") or data.get("trades") or []
    return [t for t in data if isinstance(t, dict)]


def default_path() -> Path:
    from bot.utils.paths import state_path
    return Path(state_path("data/closed_trades.json"))


def load_paper(path: Path) -> list[dict]:
    """The paper book's closed positions (its ``history``)."""
    data = json.loads(path.read_text())
    rows = data.get("history") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("no history list in the paper book")
    return [t for t in rows if isinstance(t, dict)]


def paper_path() -> Optional[Path]:
    """The operator's paper book: the first id in TELEGRAM_CHAT_ID, the book an
    unattended confirm opens on while live is off. None with no operator."""
    from bot.config import CONFIG
    from bot.utils.paths import state_path
    ids = [x.strip() for x in str(CONFIG.telegram.chat_id or "").split(",") if x.strip()]
    if not ids:
        return None
    return Path(state_path(f"data/portfolio_{ids[0]}.json"))


def fill_model_since(path: Optional[Path] = None) -> tuple[Optional[datetime], str]:
    """When the first paper confirm ran under the resting-limit fill model, and
    what the record said: ``(when, "read")``, ``(None, "absent")`` before any
    has run, or ``(None, "unreadable")``. Unreadable is never "not yet"."""
    if path is None:
        from bot.utils.paths import state_path
        path = Path(state_path(FILL_MODEL_MARKER))
    if not path.exists():
        return None, "absent"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None, "unreadable"
    when = _when(data.get("since")) if isinstance(data, dict) else None
    if when is None or data.get("model") != FILL_MODEL:
        return None, "unreadable"
    return when, "read"


def main(argv=None, now: Optional[datetime] = None,
         marker: Optional[Path] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["status", "evaluate"])
    ap.add_argument("--trades", type=Path, default=None)
    ap.add_argument("--book", choices=["live", "paper", "paper-limits"], default="live")
    a = ap.parse_args(argv)
    paper = a.book in ("paper", "paper-limits")
    since, said = fill_model_since(marker) if paper else (None, "")
    opened_from = None
    if a.book == "paper-limits":
        if since is None:
            print("the resting-limit fill model's record is "
                  + ("unreadable, so the window cannot be placed; nothing is scored"
                     if said == "unreadable" else
                     "absent: no paper confirm has run under it yet, so the window "
                     "has not opened"))
            return 3 if said == "unreadable" else 0
        start, end = since, since + timedelta(weeks=PAPER_LIMITS_WEEKS)
        opened_from = since
    else:
        start, end = (PAPER_START, PAPER_END) if paper else (START, END)
    path = a.trades or (paper_path() if paper else default_path())
    if path is None:
        print("no operator in TELEGRAM_CHAT_ID, so there is no paper book to read")
        return 3
    try:
        rows = load_paper(path) if paper else load(path)
    except (OSError, ValueError) as exc:
        print(f"{path} does not read ({type(exc).__name__}); nothing is scored from it.")
        return 3
    scored, left_out = score(rows, start, end, PAPER_NET_KEYS if paper else LIVE_NET_KEYS,
                             opened_from)
    capped = paper and len(rows) >= PAPER_HISTORY_CAP
    now = now or datetime.now(UTC)
    if a.cmd == "status":
        print(f"{a.book} book, window {start.date()} .. {end.date()} (exclusive); "
              f"scored {len(scored)}, left out {left_out}, "
              f"weeks {len({s['week'] for s in scored})}; returns are not shown before "
              f"the window closes")
        if capped:
            print(f"the paper history is at its {PAPER_HISTORY_CAP}-row cap: older closes "
                  f"may have been dropped, and the count may be short")
        if a.book == "paper":
            print(_fill_model_split_line(rows, start, end, since, said))
        return 0
    if now < end:
        print(f"refused: the window closes {end.date()}; it is evaluated once, after that")
        return 2
    res = judge(scored)
    res["book"] = a.book
    res["left_out"] = left_out
    res["history_at_cap"] = capped
    if a.book == "paper":
        # Addendum 2: Addendum 1's window spans two fill models, so its
        # reading is reported whole (its sealed rule) and split at the change.
        res["fill_model_record"] = said
        if since is not None:
            res["by_fill_model"] = {
                "filled_at_once": judge(score(rows, start, end, PAPER_NET_KEYS,
                                              opened_before=since)[0]),
                "rests_like_live": judge(score(rows, start, end, PAPER_NET_KEYS,
                                               since)[0]),
            }
    if a.book == "paper-limits":
        res["window"] = [start.isoformat(), end.isoformat()]
    print(json.dumps(res, indent=1, default=str))
    return 0


def _fill_model_split_line(rows, start, end, since, said) -> str:
    """Addendum 1's counts split at the fill-model change (Addendum 2)."""
    if since is None:
        return f"fill-model record {said}: not split"
    old = len(score(rows, start, end, PAPER_NET_KEYS, opened_before=since)[0])
    new = len(score(rows, start, end, PAPER_NET_KEYS, since)[0])
    return (f"split at the fill-model change {since.isoformat()}: {old} opened before it "
            f"(filled at once), {new} opened under resting limits")


if __name__ == "__main__":
    raise SystemExit(main())
