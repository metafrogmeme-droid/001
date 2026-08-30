"""Black Swan Detector — statistical anomaly detection for RUNECLAW trading bot.

Monitors market microstructure for early warning signs of extreme events,
triggering pre-emptive halts BEFORE the circuit breaker (which only fires
after 5 % daily loss or 10 % drawdown) would react.

Anomaly types tracked:
    - Correlation breakdown between correlated assets
    - Volume collapse / liquidity evaporation
    - Price acceleration (flash-crash detection)
    - Volatility explosion (ATR spike)
    - Bid-ask spread widening (simulated)
"""

from __future__ import annotations

from datetime import datetime
from bot.compat import UTC
from enum import Enum
from typing import Optional

import numpy as np
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class AnomalyType(str, Enum):
    """Categories of structural market anomalies."""

    CORRELATION_BREAKDOWN = "CORRELATION_BREAKDOWN"
    VOLUME_COLLAPSE = "VOLUME_COLLAPSE"
    PRICE_ACCELERATION = "PRICE_ACCELERATION"
    VOLATILITY_EXPLOSION = "VOLATILITY_EXPLOSION"
    SPREAD_WIDENING = "SPREAD_WIDENING"


def _too_flat(arr) -> bool:
    """True when a series has too little relative movement to correlate.

    Absolute std is not comparable across price levels — a $0.02 wobble is
    noise on a $25 stock token and a real move on a $0.05 alt — so this is
    measured against the mean level.
    """
    mean = float(np.mean(np.abs(arr)))
    if mean <= 0:
        return True
    return (float(np.std(arr)) / mean) < _MIN_REL_STD


class AnomalyAlert(BaseModel):
    """Immutable record of a detected anomaly."""

    anomaly_type: AnomalyType
    severity: float = Field(ge=0.0, le=1.0)  # 0 = minor, 1 = extreme
    symbol: str
    description: str
    metric_value: float  # the actual measured value
    threshold: float  # the threshold it exceeded
    recommended_action: str  # e.g. "HALT_NEW_TRADES", "REDUCE_POSITION_SIZE", "MONITOR"
    # The counterparty of a pairwise anomaly (correlation breakdown names the
    # peer that decorrelated) — lets the alert pipeline CLUSTER one market
    # event that fires against many symbols into a single page.
    peer: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Thresholds / constants
# ---------------------------------------------------------------------------

_MAX_HISTORY = 100
_CORRELATION_WINDOW = 20
_CORRELATION_THRESHOLD = 0.3
# A "breakdown" needs something to break: the pair must have been genuinely
# correlated over the PRIOR window before an uncorrelated present means
# anything. Without this gate, any two unrelated small-caps sitting near
# zero correlation — normal alt behaviour — paged at max severity.
_CORRELATION_BASELINE = 0.6
_VOLUME_WINDOW = 20
_VOLUME_COLLAPSE_RATIO = 0.30

#: A COLLAPSE NEEDS LIQUIDITY TO COLLAPSE FROM.
#:
#: `_check_volume_collapse` guards `avg_volume == 0` and not `avg_volume ≈ 0`,
#: which is the same shape as the zero-std guard below and excludes exactly the
#: case that hurts. Observed live on 2026-08-21: `EWH/USDT:USDT` — a tokenised
#: ETF perp averaging a few contracts a bar — had one bar with no trades and
#: paged
#:
#:     EWH/USDT:USDT volume collapsed to 0.0% of 20-period average
#:     Severity: 1.00 (advises HALT_NEW_TRADES)
#:
#: at the MAXIMUM severity the scale has. Three contracts a bar is not
#: liquidity that evaporated; it is a symbol that trades intermittently, and a
#: quiet bar in it is the normal case. The ratio is arithmetically correct and
#: means nothing.
#:
#: Base-asset volume is not comparable across symbols (BTC trades in tens, a
#: memecoin in millions), so the floor is expressed in QUOTE terms using the
#: latest price — roughly the turnover of one bar.
_MIN_BAR_NOTIONAL = 10_000.0

#: CORRELATION OVER A NEARLY-FLAT SERIES IS ROUNDING NOISE.
#:
#: The `std == 0` guard below rejects a perfectly constant series and admits
#: one that ticks 25.00 / 25.01 / 25.00 — whose correlation with anything is
#: dominated by the last decimal and swings between +1 and -1 between windows.
#: That is how a tokenised stock and an AI token "decorrelated": on the same
#: night, `TAO/USDT` vs `RTXSTOCK/USDT:USDT` reported a collapse from 0.776 to
#: -0.949, which is not a market event between two things that have no reason
#: to move together at all.
#:
#: Relative, not absolute, so it means the same thing at any price level.
_MIN_REL_STD = 0.0005      # 0.05% of the mean level
_PRICE_ACCEL_WINDOW = 5
_PRICE_ACCEL_LOOKBACK = 20
_PRICE_ACCEL_SIGMA = 3.0
_ATR_WINDOW = 20
_ATR_EXPLOSION_FACTOR = 3.0
_SPREAD_FACTOR = 2.0
_HALT_SEVERITY = 0.8


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Off-hours attenuation
# ---------------------------------------------------------------------------
# A tokenized equity outside its market's hours has almost no liquidity: the
# spread widens, volume collapses, prints go stale and gappy, and correlations
# against a 24/7 crypto peer break because one side of the pair is frozen. All
# four of this module's checks fire on that, and none of it is an emergency —
# it is what the instrument does when its market is shut.
#
# THIS WAS DIAGNOSED ONCE AND ANSWERED WITH A NUMBER. `_check_spread_widening`
# carries the note: the 2026-08-19 flood was BBSTOCK at 8.4x and RTXSTOCK at
# 10.6x, "tokenized equities outside their market's hours", and the fix was to
# raise the severity ceiling 8x -> 20x so they would land mid-scale. On
# 2026-08-30 (a Sunday) META reached 47.2x, DFEN 25.8x and COIN 22.6x — back
# through the new ceiling, saturating at 1.00, paging again, 78 symbols in one
# pass. The ratio is unbounded precisely BECAUSE the condition is unbounded,
# so no ceiling holds; the comment even predicts its own next move ("this
# number is the one to move").
#
# ATTENUATE, DO NOT SUPPRESS. The alert is still detected, still recorded and
# still appears in the digest — it simply stops claiming to be a market
# emergency worth waking someone for. Hiding it would be the opposite defect:
# a quiet channel that is not a claim the market is calm.
#
# Crypto is untouched — `is_market_open("Crypto")` is always True — so a real
# crypto liquidity failure still reaches 1.00 and still pages.
_OFF_HOURS_SEVERITY_CAP = _HALT_SEVERITY - 0.01


def off_hours_reason(symbol: str, now: Optional[datetime] = None) -> str:
    """Why this symbol's market is shut right now, or "" if it is open.

    FAIL-OPEN by construction: any classification or calendar fault returns
    "" (treated as open), so a symbol we cannot place is never quietly
    demoted. Missing an attenuation costs noise; a wrong one costs a page
    nobody sends.
    """
    try:
        from bot.core.market_scanner import _classify_symbol
        from bot.core.order_rules import is_market_open
        asset_class = _classify_symbol(symbol)
        is_open, reason = is_market_open(asset_class, now)
        if is_open:
            return ""
        return reason or f"{asset_class} market is closed"
    except Exception:
        return ""


def attenuate_off_hours(alert: "AnomalyAlert",
                        now: Optional[datetime] = None) -> "AnomalyAlert":
    """Scale an off-hours alert below the paging threshold and say why.

    SCALED, not clamped. `min(severity, cap)` would flatten 47x and 22x onto
    the same number and lose the ordering the digest sorts by; multiplying
    keeps the loudest one loudest while putting the whole set under
    `_HALT_SEVERITY`.
    """
    reason = off_hours_reason(alert.symbol, now)
    if not reason:
        return alert
    return alert.model_copy(update={
        "severity": round(alert.severity * _OFF_HOURS_SEVERITY_CAP, 4),
        "recommended_action": "MONITOR",
        "description": f"{alert.description} — {reason}; expected off-hours "
                       f"behaviour, not a liquidity emergency",
    })


class BlackSwanDetector:
    """Statistical anomaly detector that pre-empts the circuit breaker.

    While the circuit breaker reacts to losses (5 % daily, 10 % drawdown),
    the BlackSwanDetector watches market microstructure for signs of danger
    BEFORE positions take damage.

    Usage::

        detector = BlackSwanDetector()

        # Each scan cycle, feed data for every monitored symbol:
        alerts = detector.update("BTC/USDT", price=67_400.0, volume=1_200.0, atr=350.0)

        if detector.halt_recommended:
            # ... pause the strategy ...
            pass
    """

    def __init__(self) -> None:
        # Rolling price history per symbol: list of close prices
        self._price_history: dict[str, list[float]] = {}
        # Rolling volume history per symbol
        self._volume_history: dict[str, list[float]] = {}
        # Rolling ATR history per symbol
        self._atr_history: dict[str, list[float]] = {}
        # Alert state
        self._active_alerts: list[AnomalyAlert] = []
        self._halt_recommended: bool = False

    # -- public properties --------------------------------------------------

    @property
    def halt_recommended(self) -> bool:
        """``True`` if any active alert recommends halting new trades."""
        return self._halt_recommended

    @property
    def active_alerts(self) -> list[AnomalyAlert]:
        """Return a shallow copy of current active alerts."""
        return list(self._active_alerts)

    # -- primary interface --------------------------------------------------

    def update(
        self,
        symbol: str,
        price: float,
        volume: float,
        atr: float = 0.0,
    ) -> list[AnomalyAlert]:
        """Feed new market data and check for anomalies.

        Call this every scan cycle for each symbol being monitored.

        Args:
            symbol: Trading pair identifier (e.g. ``"BTC/USDT"``).
            price: Latest close / last price.
            volume: Latest period volume.
            atr: Current Average True Range value.  Pass ``0.0`` if unavailable.

        Returns:
            List of *new* alerts triggered during this update (empty when
            market conditions are normal).
        """
        # 1. Append to rolling histories (capped at _MAX_HISTORY)
        self._append_history(self._price_history, symbol, price)
        self._append_history(self._volume_history, symbol, volume)
        if atr > 0.0:
            self._append_history(self._atr_history, symbol, atr)

        # 2. Run all five anomaly checks for this symbol
        new_alerts: list[AnomalyAlert] = []
        for check in (
            self._check_correlation_breakdown,
            self._check_volume_collapse,
            self._check_price_acceleration,
            self._check_volatility_explosion,
            self._check_spread_widening,
        ):
            alert = check(symbol)
            if alert is not None:
                # Attenuated HERE as well as in check_all(). Both run the same
                # five checks independently — this is the live per-update path
                # and check_all() is the periodic sweep — so covering only one
                # would leave the other paging on a shut market.
                new_alerts.append(attenuate_off_hours(alert))

        # 3. Merge into active alerts
        self._active_alerts.extend(new_alerts)

        # 4. Escalate to halt if any alert is severe enough
        if any(a.severity >= _HALT_SEVERITY for a in new_alerts):
            self._halt_recommended = True

        return new_alerts

    def check_all(self) -> list[AnomalyAlert]:
        """Run all anomaly checks across every tracked symbol.

        Useful for a periodic sweep rather than per-update checking.
        """
        all_symbols = set(self._price_history.keys())
        new_alerts: list[AnomalyAlert] = []
        for symbol in all_symbols:
            for check in (
                self._check_correlation_breakdown,
                self._check_volume_collapse,
                self._check_price_acceleration,
                self._check_volatility_explosion,
                self._check_spread_widening,
            ):
                alert = check(symbol)
                if alert is not None:
                    new_alerts.append(attenuate_off_hours(alert))

        self._active_alerts.extend(new_alerts)
        if any(a.severity >= _HALT_SEVERITY for a in new_alerts):
            self._halt_recommended = True
        return new_alerts

    def clear_alerts(self) -> None:
        """Reset all active alerts and lift halt recommendation.

        This is a manual action analogous to resetting the circuit breaker.
        """
        self._active_alerts.clear()
        self._halt_recommended = False

    # -- individual anomaly checks ------------------------------------------

    def _check_correlation_breakdown(self, symbol: str) -> Optional[AnomalyAlert]:
        """Detect divergence between assets that WERE moving together.

        A breakdown needs something to break. For each peer we compute the
        correlation over the PRIOR window (bars -40..-20, the baseline) and
        over the CURRENT window (bars -20..). Only a pair whose baseline was
        >= ``_CORRELATION_BASELINE`` (0.6) and whose current correlation fell
        below ``_CORRELATION_THRESHOLD`` (0.3) is a breakdown; two symbols
        that were never correlated sitting near zero is normal market
        behaviour, not an anomaly (the old check paged max-severity for
        exactly that). Severity scales with the DROP from baseline, and
        insufficient history is silence, never a guess.
        """
        prices = self._price_history.get(symbol)
        if prices is None or len(prices) < 2 * _CORRELATION_WINDOW:
            return None

        target_now = np.array(prices[-_CORRELATION_WINDOW:])
        target_base = np.array(prices[-2 * _CORRELATION_WINDOW:-_CORRELATION_WINDOW])
        other_symbols = [s for s in self._price_history if s != symbol]
        if not other_symbols:
            return None

        worst = None   # (drop, current, baseline, peer)
        for peer in other_symbols:
            pp = self._price_history[peer]
            if len(pp) < 2 * _CORRELATION_WINDOW:
                continue
            peer_now = np.array(pp[-_CORRELATION_WINDOW:])
            peer_base = np.array(pp[-2 * _CORRELATION_WINDOW:-_CORRELATION_WINDOW])
            # Guard against constant AND near-constant series. `== 0` rejected
            # only the perfectly flat case and admitted the one that ticks in
            # the last decimal, whose correlation is rounding noise — see
            # _MIN_REL_STD.
            if any(_too_flat(a) for a in
                   (target_now, target_base, peer_now, peer_base)):
                continue
            baseline = float(np.corrcoef(target_base, peer_base)[0, 1])
            if baseline < _CORRELATION_BASELINE:
                continue   # never correlated -> nothing broke
            current = float(np.corrcoef(target_now, peer_now)[0, 1])
            if current >= _CORRELATION_THRESHOLD:
                continue
            drop = baseline - current
            if worst is None or drop > worst[0]:
                worst = (drop, current, baseline, peer)

        if worst is None:
            return None
        drop, current, baseline, peer = worst
        # THE SCALE MUST SPAN THE RANGE IT MEASURES, and this one spanned half.
        # A correlation is bounded [-1, 1], so `drop` is bounded [0, 2] — at a
        # ceiling of 1.0 ANY pair falling from a 0.6+ baseline into negative
        # territory scored a saturated 1.00. Observed live on 2026-08-19:
        # LINK/RTXSTOCK 0.724 -> -0.375 (drop 1.099), HBAR/ACE 0.635 -> -0.551
        # (1.186), ACE/NATGAS 0.700 -> -0.408 (1.108), ACE/HBAR 0.700 -> -0.598
        # (1.298). Four maximum-severity pages in sixteen minutes, none of them
        # a market emergency — ACE decorrelating from natural gas scored what a
        # total inversion of a tightly-coupled pair would.
        #
        # Everything at/above _HALT_SEVERITY takes the severe path in
        # proactive_monitor and gets its own card; the digest built to collapse
        # these never saw them. A severity whose maximum is the COMMON case
        # carries no information, and red arriving every few minutes is read as
        # decoration — which is how the next real one becomes invisible.
        #
        # The comment here used to say full severity meant "a genuinely tight
        # pair inverting". At ceiling 1.0 it did not: a tight pair inverting is
        # 1.0 -> -1.0, a drop of two. The ceiling now says what it claimed.
        severity = self._severity_from_ratio(drop, floor=0.0, ceiling=2.0)
        action = "HALT_NEW_TRADES" if severity >= _HALT_SEVERITY else "REDUCE_POSITION_SIZE"
        return AnomalyAlert(
            anomaly_type=AnomalyType.CORRELATION_BREAKDOWN,
            severity=severity,
            symbol=symbol,
            description=(
                f"Correlation between {symbol} and {peer} collapsed to "
                f"{current:.3f} (was {baseline:.3f} over the prior window; "
                f"alert threshold {_CORRELATION_THRESHOLD})"
            ),
            metric_value=current,
            threshold=_CORRELATION_THRESHOLD,
            recommended_action=action,
            peer=peer,
        )

    def _check_volume_collapse(self, symbol: str) -> Optional[AnomalyAlert]:
        """Detect sudden liquidity evaporation.

        Fires when the latest volume is below 30 % of the 20-period rolling
        average, indicating extreme slippage risk.
        """
        volumes = self._volume_history.get(symbol)
        if volumes is None or len(volumes) < _VOLUME_WINDOW + 1:
            return None

        window = np.array(volumes[-(_VOLUME_WINDOW + 1): -1])
        avg_volume = float(np.mean(window))
        if avg_volume == 0:
            return None

        # Too thin for the ratio to mean anything. See _MIN_BAR_NOTIONAL: this
        # is the `≈ 0` half of the guard above, and without it a symbol that
        # turns over a few dollars a bar pages at severity 1.00 the first time
        # a bar has no trades.
        _prices = self._price_history.get(symbol) or []
        _last_price = float(_prices[-1]) if _prices else 0.0
        if avg_volume * _last_price < _MIN_BAR_NOTIONAL:
            return None

        current_volume = volumes[-1]
        ratio = current_volume / avg_volume  # e.g. 0.25 means 25 % of average

        if ratio < _VOLUME_COLLAPSE_RATIO:
            # Severity increases as ratio drops toward 0
            severity = self._severity_from_ratio(
                _VOLUME_COLLAPSE_RATIO - ratio,
                floor=0.0,
                ceiling=_VOLUME_COLLAPSE_RATIO,
            )
            action = "HALT_NEW_TRADES" if severity >= _HALT_SEVERITY else "REDUCE_POSITION_SIZE"
            return AnomalyAlert(
                anomaly_type=AnomalyType.VOLUME_COLLAPSE,
                severity=severity,
                symbol=symbol,
                description=(
                    f"{symbol} volume collapsed to {ratio:.1%} of 20-period average "
                    f"(threshold {_VOLUME_COLLAPSE_RATIO:.0%})"
                ),
                metric_value=ratio,
                threshold=_VOLUME_COLLAPSE_RATIO,
                recommended_action=action,
            )
        return None

    def _check_price_acceleration(self, symbol: str) -> Optional[AnomalyAlert]:
        """Detect moves exceeding 3 standard deviations within a short window.

        Computes log-returns over the last 20 periods to establish a
        volatility baseline, then checks whether the absolute price change
        over the most recent 5 bars exceeds ``3 * std_dev``.  This catches
        flash crashes and cascade liquidations.
        """
        prices = self._price_history.get(symbol)
        if prices is None or len(prices) < _PRICE_ACCEL_LOOKBACK + 1:
            return None

        arr = np.array(prices[-(max(_PRICE_ACCEL_LOOKBACK, _PRICE_ACCEL_WINDOW) + 1):])
        log_returns = np.diff(np.log(arr))

        if len(log_returns) < _PRICE_ACCEL_LOOKBACK:
            return None

        std_dev = float(np.std(log_returns[-_PRICE_ACCEL_LOOKBACK:]))
        if std_dev == 0:
            return None

        # Absolute return over the most recent PRICE_ACCEL_WINDOW bars
        recent_move = abs(float(np.sum(log_returns[-_PRICE_ACCEL_WINDOW:])))
        sigma_multiple = recent_move / std_dev

        if sigma_multiple > _PRICE_ACCEL_SIGMA:
            severity = self._severity_from_ratio(
                sigma_multiple,
                floor=_PRICE_ACCEL_SIGMA,
                ceiling=_PRICE_ACCEL_SIGMA * 3,  # 9-sigma → severity 1.0
            )
            action = "HALT_NEW_TRADES" if severity >= _HALT_SEVERITY else "REDUCE_POSITION_SIZE"
            return AnomalyAlert(
                anomaly_type=AnomalyType.PRICE_ACCELERATION,
                severity=severity,
                symbol=symbol,
                description=(
                    f"{symbol} moved {sigma_multiple:.1f} sigma in {_PRICE_ACCEL_WINDOW} bars "
                    f"(threshold {_PRICE_ACCEL_SIGMA:.0f} sigma)"
                ),
                metric_value=sigma_multiple,
                threshold=_PRICE_ACCEL_SIGMA,
                recommended_action=action,
            )
        return None

    def _check_volatility_explosion(self, symbol: str) -> Optional[AnomalyAlert]:
        """Detect ATR spiking to 3x or more of its 20-period average.

        When the market enters a volatility regime that is 3x above its
        recent norm, standard position-sizing assumptions break down and
        the bot should reduce exposure or halt.
        """
        atrs = self._atr_history.get(symbol)
        if atrs is None or len(atrs) < _ATR_WINDOW + 1:
            return None

        window = np.array(atrs[-(_ATR_WINDOW + 1): -1])
        avg_atr = float(np.mean(window))
        if avg_atr == 0:
            return None

        current_atr = atrs[-1]
        ratio = current_atr / avg_atr

        if ratio > _ATR_EXPLOSION_FACTOR:
            severity = self._severity_from_ratio(
                ratio,
                floor=_ATR_EXPLOSION_FACTOR,
                ceiling=_ATR_EXPLOSION_FACTOR * 3,  # 9x → severity 1.0
            )
            action = "HALT_NEW_TRADES" if severity >= _HALT_SEVERITY else "REDUCE_POSITION_SIZE"
            return AnomalyAlert(
                anomaly_type=AnomalyType.VOLATILITY_EXPLOSION,
                severity=severity,
                symbol=symbol,
                description=(
                    f"{symbol} ATR spiked to {ratio:.1f}x its 20-period average "
                    f"(threshold {_ATR_EXPLOSION_FACTOR:.0f}x)"
                ),
                metric_value=ratio,
                threshold=_ATR_EXPLOSION_FACTOR,
                recommended_action=action,
            )
        return None

    def _check_spread_widening(self, symbol: str) -> Optional[AnomalyAlert]:
        """Detect (simulated) bid-ask spread exceeding 2x normal.

        Real spread data requires Level-2 order-book feeds.  As a proxy we
        estimate the spread from recent price volatility: the spread is
        modelled as the standard deviation of tick-to-tick returns over the
        last 20 bars scaled by a noise factor.  When the latest estimated
        spread exceeds ``_SPREAD_FACTOR`` (2x) its rolling baseline, market
        makers are likely pulling liquidity.
        """
        prices = self._price_history.get(symbol)
        if prices is None or len(prices) < _CORRELATION_WINDOW + 1:
            return None

        arr = np.array(prices[-(max(_CORRELATION_WINDOW, 1) + 1):])
        returns = np.abs(np.diff(arr) / arr[:-1])
        if len(returns) < _CORRELATION_WINDOW:
            return None

        baseline = float(np.mean(returns[:-1])) if len(returns) > 1 else 0.0
        if baseline == 0:
            return None

        latest = float(returns[-1])
        ratio = latest / baseline

        if ratio > _SPREAD_FACTOR:
            # EMPIRICAL, unlike the correlation ceiling above — a spread ratio
            # is unbounded, so no arithmetic argument fixes the top of the
            # scale. What fixed it here is that 8x was reached constantly: the
            # 2026-08-19 flood included BBSTOCK at 8.4x and RTXSTOCK at 10.6x,
            # both saturating at 1.00. Those are tokenized equities outside
            # their market's hours, where a spread several times baseline is
            # what the instrument does, not an emergency.
            #
            # A 20x ceiling leaves both of those mid-scale (0.35, 0.48) and in
            # the digest, while a genuine liquidity failure still reaches 1.00.
            # This is a calibration judgement on observed data, not a proof —
            # if a real 10x blowout is later judged page-worthy, this number is
            # the one to move, and moving it changes no trading behaviour.
            severity = self._severity_from_ratio(
                ratio,
                floor=_SPREAD_FACTOR,
                ceiling=_SPREAD_FACTOR * 10,  # 20x → severity 1.0
            )
            action = "HALT_NEW_TRADES" if severity >= _HALT_SEVERITY else "MONITOR"
            return AnomalyAlert(
                anomaly_type=AnomalyType.SPREAD_WIDENING,
                severity=severity,
                symbol=symbol,
                description=(
                    f"{symbol} estimated spread widened to {ratio:.1f}x baseline "
                    f"(threshold {_SPREAD_FACTOR:.0f}x)"
                ),
                metric_value=ratio,
                threshold=_SPREAD_FACTOR,
                recommended_action=action,
            )
        return None

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _severity_from_ratio(ratio: float, floor: float = 0.0, ceiling: float = 1.0) -> float:
        """Map a ratio to a 0-1 severity score with linear interpolation.

        ``floor`` is the ratio value that maps to severity 0.
        ``ceiling`` is the ratio value that maps to severity 1.
        Values outside the range are clamped.
        """
        if ceiling == floor:
            return 1.0 if ratio >= ceiling else 0.0
        raw = (ratio - floor) / (ceiling - floor)
        return float(np.clip(raw, 0.0, 1.0))

    def _append_history(
        self,
        store: dict[str, list[float]],
        symbol: str,
        value: float,
    ) -> None:
        """Append *value* to the per-symbol rolling buffer, capping at ``_MAX_HISTORY``."""
        buf = store.setdefault(symbol, [])
        buf.append(value)
        if len(buf) > _MAX_HISTORY:
            del buf[: len(buf) - _MAX_HISTORY]
