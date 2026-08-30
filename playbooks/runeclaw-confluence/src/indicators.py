"""Deterministic feature + confluence math shared by replay and live paths.

Single source of truth: the Nautilus replay strategy feeds these functions from
rolling buffers and the live scan feeds them from fetched klines, so both paths
score a bar identically. Everything uses same-or-past bars only.

The live RUNECLAW system blends this electorate with an LLM assessment; the
LLM layer is live-context only and cannot be fairly replayed, so this package
is the deterministic electorate renormalized to the full scale, with the same
hard gate and the same flat-by-default behavior (an unreadable input scores
nothing rather than something).
"""
import math
from typing import Optional, Sequence

# Voter weights (sum 100). Order-book and cross-sectional breadth voters of the
# live system are not replayable for a single symbol and are dropped; the
# remaining electorate carries the full scale.
W_MTF = 20.0
W_RSI = 15.0
W_MACD = 15.0
W_VWAP = 10.0
W_RANGE = 10.0
W_OBV = 10.0
W_VOLUME = 10.0
W_BB = 10.0


def ema_series(values: Sequence[float], period: int) -> Optional[float]:
    """EMA of the last value given the full window; None until enough data."""
    if period <= 0 or len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1.0 - k)
    return ema


def rsi(closes: Sequence[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0.0)) / period
    if avg_loss <= 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def macd_hist(closes: Sequence[float], fast: int = 12, slow: int = 26, sig: int = 9) -> tuple:
    """Return (hist_now, hist_prev) or (None, None)."""
    need = slow + sig + 1
    if len(closes) < need + 1:
        return (None, None)

    def _macd_line(seq: Sequence[float]) -> Optional[list]:
        if len(seq) < slow:
            return None
        k_f = 2.0 / (fast + 1.0)
        k_s = 2.0 / (slow + 1.0)
        ema_f = sum(seq[:fast]) / fast
        ema_s = sum(seq[:slow]) / slow
        line = []
        for i, v in enumerate(seq):
            if i >= fast:
                ema_f = v * k_f + ema_f * (1.0 - k_f)
            if i >= slow:
                ema_s = v * k_s + ema_s * (1.0 - k_s)
                line.append(ema_f - ema_s)
        return line

    line = _macd_line(list(closes))
    if not line or len(line) < sig + 2:
        return (None, None)
    k_g = 2.0 / (sig + 1.0)
    sig_ema = sum(line[:sig]) / sig
    hist_prev = None
    hist_now = None
    for v in line[sig:]:
        sig_ema = v * k_g + sig_ema * (1.0 - k_g)
        hist_prev = hist_now
        hist_now = v - sig_ema
    return (hist_now, hist_prev)


def bollinger_pct_b(closes: Sequence[float], period: int = 20,
                    mult: float = 2.0) -> Optional[float]:
    if len(closes) < period:
        return None
    window = list(closes[-period:])
    mean = sum(window) / period
    var = sum((c - mean) ** 2 for c in window) / period
    sd = math.sqrt(var)
    if sd <= 0:
        return 0.5
    upper = mean + mult * sd
    lower = mean - mult * sd
    return (window[-1] - lower) / (upper - lower)


def rolling_vwap(highs, lows, closes, volumes, window: int) -> Optional[float]:
    n = len(closes)
    if n < window:
        return None
    pv = 0.0
    sv = 0.0
    for i in range(n - window, n):
        typical = (highs[i] + lows[i] + closes[i]) / 3.0
        pv += typical * volumes[i]
        sv += volumes[i]
    if sv <= 0:
        return None
    return pv / sv


def atr(highs, lows, closes, period: int = 14) -> Optional[float]:
    n = len(closes)
    if n < period + 1:
        return None
    trs = []
    for i in range(1, n):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    out = sum(trs[:period]) / period
    for tr in trs[period:]:
        out = (out * (period - 1) + tr) / period
    return out


def obv_slope(closes, volumes, lookback: int = 20) -> Optional[float]:
    n = len(closes)
    if n < lookback + 2:
        return None
    obv = 0.0
    series = [0.0]
    for i in range(1, n):
        if closes[i] > closes[i - 1]:
            obv += volumes[i]
        elif closes[i] < closes[i - 1]:
            obv -= volumes[i]
        series.append(obv)
    return series[-1] - series[-1 - lookback]


def volume_ratio(volumes, baseline: int = 20) -> Optional[float]:
    if len(volumes) < baseline + 1:
        return None
    base = sum(volumes[-baseline - 1:-1]) / baseline
    if base <= 0:
        return None
    return volumes[-1] / base


class Features:
    """Per-bar deterministic feature snapshot for one symbol."""

    __slots__ = ("ok", "close", "vwap", "hi", "lo", "atr", "atrp", "chg", "range_pos",
                 "rsi", "macd_hist", "macd_hist_prev", "pct_b", "obv_slope", "vol_ratio",
                 "ema20", "ema50", "ema20_1h", "ema50_1h", "ema20_4h")

    def __init__(self, **kw) -> None:
        for name in self.__slots__:
            setattr(self, name, kw.get(name))


def compute_features(highs, lows, closes, volumes, window: int = 96) -> Features:
    """Compute the full feature snapshot from aligned OHLCV sequences (15m bars)."""
    n = len(closes)
    if n < window + 1:
        return Features(ok=False)
    close = closes[-1]
    vwap_v = rolling_vwap(highs, lows, closes, volumes, window)
    hi = max(highs[-window:])
    lo = min(lows[-window:])
    prior = closes[-window - 1]
    chg = (close / prior - 1.0) if prior else None
    atr_v = atr(highs, lows, closes, 14)
    span = hi - lo
    range_pos = ((close - lo) / span) if span > 0 else None

    agg_1h = [closes[i] for i in range(n - 1, -1, -4)][::-1]
    agg_4h = [closes[i] for i in range(n - 1, -1, -16)][::-1]

    return Features(
        ok=(vwap_v is not None and atr_v is not None and chg is not None),
        close=close, vwap=vwap_v, hi=hi, lo=lo, atr=atr_v,
        atrp=((atr_v / close) if (atr_v is not None and close) else None),
        chg=chg, range_pos=range_pos,
        rsi=rsi(closes, 14),
        macd_hist=macd_hist(closes)[0], macd_hist_prev=macd_hist(closes)[1],
        pct_b=bollinger_pct_b(closes, 20, 2.0),
        obv_slope=obv_slope(closes, volumes, 20),
        vol_ratio=volume_ratio(volumes, 20),
        ema20=ema_series(closes, 20), ema50=ema_series(closes, 50),
        ema20_1h=ema_series(agg_1h, 20), ema50_1h=ema_series(agg_1h, 50),
        ema20_4h=ema_series(agg_4h, 20),
    )


def classify_regime(btc: Features, cfg: dict) -> dict:
    """Market-leader regime: TREND_UP / TREND_DOWN / RANGE / CHOP.

    An unreadable leader is CHOP (stand aside), never a default direction.
    """
    if not btc.ok or btc.chg is None or btc.vwap is None:
        return {"state": "CHOP", "dir": 0, "reason": "leader_unreadable"}
    trend_min = float(cfg.get("regime_trend_min_pct", "1.5")) / 100.0
    high_vol = float(cfg.get("regime_high_vol_atrp", "0.03"))
    above = btc.close > btc.vwap
    below = btc.close < btc.vwap
    ema_up = (btc.ema20_1h is not None and btc.ema50_1h is not None
              and btc.ema20_1h > btc.ema50_1h)
    ema_dn = (btc.ema20_1h is not None and btc.ema50_1h is not None
              and btc.ema20_1h < btc.ema50_1h)
    if btc.chg >= trend_min and above and ema_up:
        return {"state": "TREND_UP", "dir": 1, "reason": "leader_up"}
    if btc.chg <= -trend_min and below and ema_dn:
        return {"state": "TREND_DOWN", "dir": -1, "reason": "leader_down"}
    if abs(btc.chg) < trend_min and (btc.atrp is not None and btc.atrp < high_vol):
        return {"state": "RANGE", "dir": 0, "reason": "leader_flat"}
    return {"state": "CHOP", "dir": 0, "reason": "leader_mixed"}


def confluence_score(f: Features, side: str, mode: str) -> tuple:
    """0-100 confluence for ``side`` ("long"/"short") under ``mode``
    ("momentum" or "mr"). Missing voter input scores zero for that voter —
    absence is never a vote. Returns (score, dims)."""
    long_side = side == "long"
    dims = {}

    mtf = 0.0
    if f.ema20 is not None and f.ema50 is not None:
        if (f.ema20 > f.ema50) if long_side else (f.ema20 < f.ema50):
            mtf += 6.0
    if f.ema20_1h is not None and f.ema50_1h is not None:
        if (f.ema20_1h > f.ema50_1h) if long_side else (f.ema20_1h < f.ema50_1h):
            mtf += 7.0
    if f.ema20_4h is not None and f.close is not None:
        if (f.close > f.ema20_4h) if long_side else (f.close < f.ema20_4h):
            mtf += 7.0
    if mode == "mr":
        # Mean reversion trades against the local push; MTF weight goes to the
        # higher frames only (the range thesis needs the big frame flat-to-agree,
        # not the last few bars).
        mtf = min(mtf, 14.0) * (W_MTF / 14.0) * 0.5
    dims["mtf"] = round(mtf, 2)

    r = f.rsi
    rsi_score = 0.0
    if r is not None:
        if mode == "momentum":
            sweet = (45.0 <= r <= 70.0) if long_side else (30.0 <= r <= 55.0)
            edge = (40.0 <= r < 45.0 or 70.0 < r <= 75.0) if long_side else \
                   (55.0 < r <= 60.0 or 25.0 <= r < 30.0)
            rsi_score = W_RSI if sweet else (W_RSI * 0.5 if edge else 0.0)
        else:
            ext = (r <= 32.0) if long_side else (r >= 68.0)
            near = (32.0 < r <= 38.0) if long_side else (62.0 <= r < 68.0)
            rsi_score = W_RSI if ext else (W_RSI * 0.5 if near else 0.0)
    dims["rsi"] = round(rsi_score, 2)

    macd_score = 0.0
    if f.macd_hist is not None:
        agrees = (f.macd_hist > 0) if long_side else (f.macd_hist < 0)
        if mode == "mr":
            # For reversion the histogram should be fading against the stretch.
            if f.macd_hist_prev is not None:
                fading = (f.macd_hist > f.macd_hist_prev) if long_side else \
                         (f.macd_hist < f.macd_hist_prev)
                macd_score = W_MACD if fading else 0.0
        else:
            macd_score = 9.0 if agrees else 0.0
            if agrees and f.macd_hist_prev is not None:
                rising = (f.macd_hist > f.macd_hist_prev) if long_side else \
                         (f.macd_hist < f.macd_hist_prev)
                if rising:
                    macd_score += 6.0
    dims["macd"] = round(macd_score, 2)

    vwap_score = 0.0
    if f.vwap is not None and f.close is not None:
        above = f.close > f.vwap * 1.001
        below = f.close < f.vwap * 0.999
        if mode == "momentum":
            right = above if long_side else below
            mid = not above and not below
            vwap_score = W_VWAP if right else (W_VWAP * 0.5 if mid else 0.0)
        else:
            stretched = below if long_side else above
            vwap_score = W_VWAP if stretched else 0.0
    dims["vwap"] = round(vwap_score, 2)

    range_score = 0.0
    if f.range_pos is not None:
        rp = f.range_pos
        if mode == "momentum":
            strong = rp > 0.66 if long_side else rp < 0.34
            mid = 0.33 <= rp <= 0.67
            range_score = W_RANGE if strong else (W_RANGE * 0.5 if mid else 0.0)
        else:
            deep = rp < 0.15 if long_side else rp > 0.85
            near = rp < 0.30 if long_side else rp > 0.70
            range_score = W_RANGE if deep else (W_RANGE * 0.5 if near else 0.0)
    dims["range"] = round(range_score, 2)

    obv_score = 0.0
    if f.obv_slope is not None:
        if mode == "momentum":
            agrees = (f.obv_slope > 0) if long_side else (f.obv_slope < 0)
            obv_score = W_OBV if agrees else 0.0
        else:
            # Reversion wants the selling (buying) pressure already easing.
            obv_score = W_OBV * 0.5
    dims["obv"] = round(obv_score, 2)

    vol_score = 0.0
    if f.vol_ratio is not None:
        if f.vol_ratio >= 1.2:
            vol_score = W_VOLUME
        elif f.vol_ratio >= 0.8:
            vol_score = W_VOLUME * 0.5
    dims["volume"] = round(vol_score, 2)

    bb_score = 0.0
    if f.pct_b is not None:
        b = f.pct_b
        if mode == "momentum":
            inside = (0.5 <= b <= 0.95) if long_side else (0.05 <= b <= 0.5)
            bb_score = W_BB if inside else 0.0
        else:
            ext = (b <= 0.05) if long_side else (b >= 0.95)
            near = (b <= 0.15) if long_side else (b >= 0.85)
            bb_score = W_BB if ext else (W_BB * 0.5 if near else 0.0)
    dims["bb"] = round(bb_score, 2)

    total = (mtf + rsi_score + macd_score + vwap_score + range_score
             + obv_score + vol_score + bb_score)
    # Renormalize to the mode's achievable maximum (family convention: dropped
    # or capped voters hand their weight back so the hard gate means the same
    # thing in every mode). Momentum max is the full scale; the reversion mode
    # caps the MTF voter and fixes the OBV voter at half weight.
    max_total = 100.0 if mode == "momentum" else 85.0
    total = total * (100.0 / max_total)
    dims["total"] = round(total, 2)
    return total, dims
