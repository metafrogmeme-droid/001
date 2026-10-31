#!/usr/bin/env python3
"""Equal-size positions against the bot's own sizing, forward, as sealed.

Design and rule: docs/research/prereg/equal_sizing_forward_2026-10/PREREGISTRATION.md.

    python3 scripts/sizing_forward.py status     # counts only, any time
    python3 scripts/sizing_forward.py evaluate   # once, after the window closes
    python3 scripts/sizing_forward.py status --book paper   # Addendum 1

Reads the operator's live closes (``state_path("data/closed_trades.json")``,
or ``--trades``). With ``--book paper`` (Addendum 1, sealed 2026-10-31) it reads
the operator's paper book instead, ``data/portfolio_<operator>.json``'s
``history``, over its own window. It changes nothing in the bot.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
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
          net_keys: tuple = LIVE_NET_KEYS) -> tuple[list[dict], dict]:
    """The window's scored closes ``{week, net, notional}`` and the count of
    rows left out, by reason. Nothing unreadable is scored as zero."""
    scored, left_out = [], {"no_close_time": 0, "no_net": 0, "no_notional": 0}
    for t in rows:
        when = _when(t.get("closed_at")) if t.get("closed_at") else None
        if when is None:
            left_out["no_close_time"] += 1
            continue
        if not (start <= when < end):
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


def main(argv=None, now: Optional[datetime] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["status", "evaluate"])
    ap.add_argument("--trades", type=Path, default=None)
    ap.add_argument("--book", choices=["live", "paper"], default="live")
    a = ap.parse_args(argv)
    paper = a.book == "paper"
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
    scored, left_out = score(rows, start, end, PAPER_NET_KEYS if paper else LIVE_NET_KEYS)
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
        return 0
    if now < end:
        print(f"refused: the window closes {end.date()}; it is evaluated once, after that")
        return 2
    res = judge(scored)
    res["book"] = a.book
    res["left_out"] = left_out
    res["history_at_cap"] = capped
    print(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
