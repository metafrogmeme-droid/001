# -*- coding: utf-8 -*-
"""RWA session-gap reversion study — REAL Bitget 30m candles, no simulation.

Thesis (operator's): tokenized equity perps trade 24/7; the underlying trades
~6.5h weekdays. Returns accumulated while the cash market is CLOSED (no
arbitrage anchor) should revert during the following OPEN session.

Method: for each NYSE trading day D and symbol S,
  closed_ret = open(D, 09:30 ET) / close(D-1, 16:00 ET) - 1
  open_ret   = close(D, 16:00 ET) / open(D, 09:30 ET) - 1
Test: correlation/OLS of open_ret on closed_ret (thesis: negative), and a
fade-the-gap rule: position = -sign(closed_ret) when |closed_ret| >= filter,
earning position * open_ret, minus Bitget taker fees (0.06% x2).
"""
import json, time, subprocess, statistics, math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')
UTC = timezone.utc
SYMS = ['TSLAUSDT', 'NVDAUSDT', 'AAPLUSDT', 'MSFTUSDT', 'AMZNUSDT', 'GOOGLUSDT', 'METAUSDT', 'QQQUSDT']
LIST_MS = 1769994062288           # Feb 2 2026 listing (from contract metadata)
FEE_RT = 0.0012                   # taker 0.06% in + out
HOLIDAYS_2026 = {'2026-02-16', '2026-04-03', '2026-05-25', '2026-06-19', '2026-07-03'}

import os, tempfile
CACHE_DIR = os.environ.get('RWA_CACHE_DIR', tempfile.gettempdir())
def fetch(sym):
    """Page history-candles (200/req) from now back to listing, disk-cached."""
    cf = os.path.join(CACHE_DIR, 'cndl_%s.json' % sym)
    if os.path.exists(cf):
        return {int(k): tuple(v) for k, v in json.load(open(cf)).items()}
    out = {}
    end = int(time.time() * 1000)
    for _ in range(80):
        url = (f'https://api.bitget.com/api/v2/mix/market/history-candles'
               f'?symbol={sym}&productType=USDT-FUTURES&granularity=30m&limit=200&endTime={end}')
        rows = None
        for attempt in range(4):
            r_ = subprocess.run(['curl', '-sS', '-m', '25', url], capture_output=True, text=True)
            try:
                rows = json.loads(r_.stdout)['data']
                break
            except (json.JSONDecodeError, KeyError):
                time.sleep(1.5 * (attempt + 1))
        if rows is None:
            raise RuntimeError('fetch failed for %s after retries' % sym)
        if not rows:
            break
        for c in rows:
            out[int(c[0])] = (float(c[1]), float(c[4]))   # ts -> (open, close)
        oldest = min(int(c[0]) for c in rows)
        if oldest <= LIST_MS or oldest >= end:
            break
        end = oldest
        time.sleep(0.25)
    json.dump(out, open(cf, 'w'))
    return out

def sessions(candles):
    """Yield (date, closed_ret, open_ret) for consecutive NYSE trading days."""
    days = []
    d = datetime(2026, 2, 2, tzinfo=NY).date()
    today = datetime.now(NY).date()
    while d < today:                       # exclude today (session may be live)
        if d.weekday() < 5 and str(d) not in HOLIDAYS_2026:
            o = datetime(d.year, d.month, d.day, 9, 30, tzinfo=NY).astimezone(UTC)
            c = datetime(d.year, d.month, d.day, 16, 0, tzinfo=NY).astimezone(UTC)
            o_ms, c_ms = int(o.timestamp() * 1000), int(c.timestamp() * 1000)
            # open px = OPEN of the 09:30 candle; close px = OPEN of the 16:00
            # candle (the traded price AT the boundary, not 30 minutes later).
            if o_ms in candles and c_ms in candles:
                days.append((d, candles[o_ms][0], candles[c_ms][0]))
        d += timedelta(days=1)
    out = []
    for i in range(1, len(days)):
        (d0, _, c0), (d1, o1, c1) = days[i - 1], days[i]
        if c0 > 0 and o1 > 0:
            out.append((d1, o1 / c0 - 1, c1 / o1 - 1, (d1 - d0).days >= 3))
    return out

def corr(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)

def tstat(vals):
    n = len(vals)
    if n < 3:
        return 0.0
    m = sum(vals) / n
    sd = statistics.stdev(vals)
    return m / (sd / math.sqrt(n)) if sd > 0 else 0.0

def main() -> int:
    """The study. Under `main()` because this WAS all top-level code.

    `tests/unreachable_baseline.txt` recorded these two scripts as having no
    `__main__` guard "so they cannot be run at all". That was the wrong way
    round — they ran fine as scripts, and the guard's absence meant something
    worse: IMPORTING this module ran the whole study. Every `curl` to Bitget,
    every print, minutes of it, fired on import — including from the
    reachability checker that was cataloguing it.

    A study is a thing you ask for, not a side effect of reading the file.
    """
    ALL = []
    print(f'{"symbol":<10} {"days":>4} {"corr":>7} {"slope":>7}  gross-fade (all)   net@|gap|>=0.5%')
    for sym in SYMS:
        candles = fetch(sym)
        rows = sessions(candles)
        if len(rows) < 30:
            print(f'{sym:<10} {len(rows):>4}  — too little data, skipped')
            continue
        gaps = [r[1] for r in rows]
        opens = [r[2] for r in rows]
        c = corr(gaps, opens)
        var = sum((g - sum(gaps) / len(gaps)) ** 2 for g in gaps)
        cov = sum((g - sum(gaps) / len(gaps)) * (o - sum(opens) / len(opens)) for g, o in zip(gaps, opens))
        slope = cov / var if var else 0.0
        fade_all = [-math.copysign(1, g) * o for g, o, _w in [(r[1], r[2], r[3]) for r in rows] if g != 0]
        filt = [(g, o, w) for (d, g, o, w) in rows if abs(g) >= 0.005]
        fade_net = [-math.copysign(1, g) * o - FEE_RT for g, o, w in filt]
        ALL.extend([(sym, d, g, o, w) for (d, g, o, w) in rows])
        print(f'{sym:<10} {len(rows):>4} {c:>7.3f} {slope:>7.3f}  '
              f'mean {sum(fade_all)/len(fade_all)*100:+.3f}%/d t={tstat(fade_all):+.2f}   '
              f'n={len(fade_net):>3} mean {((sum(fade_net)/len(fade_net)) if fade_net else 0)*100:+.3f}% t={tstat(fade_net):+.2f}')

    if not ALL:
        # Every symbol skipped or unreadable. The pooled block below divides by
        # len(ALL) and would raise on the way to a report; an empty study is
        # not a finding, and a traceback is not a result.
        print('\nNo sessions measured — the study read nothing.')
        return 1

    # pooled
    gaps = [g for (_, _, g, o, w) in ALL]
    opens = [o for (_, _, g, o, w) in ALL]
    print(f'\nPOOLED n={len(ALL)}  corr={corr(gaps, opens):+.3f}')
    for label, sel in [('all days', ALL),
                       ('weekend gaps (Mon)', [r for r in ALL if r[4]]),
                       ('weekday gaps', [r for r in ALL if not r[4]])]:
        if not sel:
            continue
        fades = [-math.copysign(1, g) * o for (_, _, g, o, _w) in sel if g != 0]
        print(f'  {label:<20} n={len(fades):>4} gross fade mean {sum(fades)/len(fades)*100:+.3f}%/d  t={tstat(fades):+.2f}  hit {sum(1 for f in fades if f > 0)/len(fades)*100:.0f}%')
    for thr in (0.003, 0.005, 0.01):
        sel = [(g, o) for (_, _, g, o, _w) in ALL if abs(g) >= thr]
        if not sel:
            continue
        net = [-math.copysign(1, g) * o - FEE_RT for g, o in sel]
        print(f'  net fade @|gap|>={thr*100:.1f}%: n={len(net):>4} mean {sum(net)/len(net)*100:+.3f}%  t={tstat(net):+.2f}  hit {sum(1 for f in net if f > 0)/len(net)*100:.0f}%')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
