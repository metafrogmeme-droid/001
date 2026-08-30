# -*- coding: utf-8 -*-
"""RWA perp funding study — where does the missing arbitrage anchor express?

8h funding at 00/08/16 UTC. The 16:00 UTC print lands INSIDE the NYSE session
(11:00/12:00 ET); 00:00 and 08:00 land while cash is closed. If the anchor
expresses in funding, closed-hours prints should differ from the in-session
print, and funding should correlate with the accumulated gap.
"""
import json
import time
import subprocess
from datetime import datetime, timezone

SYMS = ['TSLAUSDT', 'NVDAUSDT', 'AAPLUSDT', 'MSFTUSDT', 'AMZNUSDT', 'GOOGLUSDT', 'METAUSDT', 'QQQUSDT']
LIST_MS = 1769994062288

def fetch_funding(sym):
    rows = []
    page = 1
    while page <= 20:
        url = (f'https://api.bitget.com/api/v2/mix/market/history-fund-rate'
               f'?symbol={sym}&productType=USDT-FUTURES&pageSize=100&pageNo={page}')
        for attempt in range(4):
            r_ = subprocess.run(['curl', '-sS', '-m', '25', url], capture_output=True, text=True)
            try:
                data = json.loads(r_.stdout)['data']
                break
            except (json.JSONDecodeError, KeyError):
                data = None
                time.sleep(1.5 * (attempt + 1))
        if data is None:
            raise RuntimeError('fetch failed ' + sym)
        if not data:
            break
        rows.extend((int(x['fundingTime']), float(x['fundingRate'])) for x in data)
        if int(data[-1]['fundingTime']) <= LIST_MS:
            break
        page += 1
        time.sleep(0.2)
    return sorted(set(rows))

def main() -> int:
    """The study. Under `main()` because this WAS all top-level code.

    `tests/unreachable_baseline.txt` recorded these two scripts as having no
    `__main__` guard "so they cannot be run at all". That was the wrong way
    round — they ran fine as scripts, and the guard's absence meant something
    worse: IMPORTING this module ran the whole study. Every `curl` to Bitget,
    every print, minutes of it, fired on import — including from the
    reachability checker that was cataloguing it, and from anything else that
    walks the tree importing what it finds.

    A study is a thing you ask for. It should not be a side effect of reading
    the file.
    """
    print(f'{"symbol":<10} {"prints":>6} {"nonzero":>8} {"mean(8h)":>10} {"|max|":>9}  by UTC print hour (mean bps)')
    grand = []
    for sym in SYMS:
        rows = [r for r in fetch_funding(sym) if r[0] >= LIST_MS]
        rates = [r[1] for r in rows]
        nz = [r for r in rates if r != 0]
        byhour = {}
        for ts, r in rows:
            h = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).hour
            byhour.setdefault(h, []).append(r)
        hh = '  '.join(f'{h:02d}h:{(sum(v)/len(v))*10000:+.2f}' for h, v in sorted(byhour.items()))
        print(f'{sym:<10} {len(rows):>6} {len(nz):>8} {sum(rates)/len(rates)*100:>9.4f}% {max((abs(r) for r in rates), default=0)*100:>8.4f}%  {hh}')
        grand.extend(rates)

    if not grand:
        # No prints read means the venue answered with nothing, and the
        # divisions below would raise. An empty study is not a finding.
        print('\nNo funding prints returned — the study measured nothing.')
        return 1

    nzg = [r for r in grand if r != 0]
    print(f'\nALL: {len(grand)} prints, {len(nzg)} nonzero ({len(nzg)/len(grand)*100:.0f}%), '
          f'mean {sum(grand)/len(grand)*100:+.5f}%/8h ({sum(grand)/len(grand)*3*365*100:+.1f}%/yr), '
          f'mean |rate| {sum(abs(r) for r in grand)/len(grand)*100:.5f}%')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
