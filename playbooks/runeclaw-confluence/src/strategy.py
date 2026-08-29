"""Nautilus replay strategy for RUNECLAW Confluence.

Self-contained and multi-instrument: subscribes to BNBUSDT (traded) and
BTCUSDT (market-leader regime context, never traded), computes every decision
input internally from rolling buffers via ``indicators.py`` (the same math the
live path uses), and routes between three entry families behind the leader
regime: momentum pullbacks in trends, average-price reversion in ranges, and a
volume-spike breakout overlay. Flat is the default state; nothing is placed
below the hard confluence gate or during indicator warmup (warmup trading gate
retained from the prior draft line).

Risk model (the point of this rewrite):
* every entry is sized so worst-case loss at the stop is bounded by
  ``max_loss_usdt`` (quantity rounded DOWN, stop distance floored),
* protection is a REAL stop-market order resting at the venue from the moment
  the position opens — not a bar-close check that can slip past the stop,
* a take-profit limit rests alongside; whichever fills first, the other is
  cancelled on position close,
* an intraday circuit breaker pauses entries after a daily loss and stops the
  day after a deeper one; an unreadable realized-PnL feed disables the breaker
  visibly (printed) rather than silently counting zero,
* unfilled entries expire, positions carry a time stop, and the stop lifts to
  entry once the trade has moved favourably.

Instance attributes are ``rc_``-prefixed to avoid colliding with reserved
NautilusTrader ``Component``/``Strategy`` internals.
"""
from collections import deque
from decimal import ROUND_DOWN, Decimal
from typing import Optional

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy

from .indicators import classify_regime, compute_features, confluence_score

_BUF = 420          # bars kept per symbol (covers the deepest EMA aggregation)
_WINDOW = 96        # 24h of 15m bars: VWAP / range / change window
_NS_PER_DAY = 86_400_000_000_000


class RuneclawConfluenceStrategyConfig(StrategyConfig):
    instrument_id: Optional[InstrumentId] = None
    bar_type: Optional[BarType] = None
    instrument_ids: tuple = ()
    bar_types: tuple = ()
    leverage: int = 10
    margin_budget: str = "100"
    max_loss_usdt: str = "15"
    min_score: int = 70
    allow_short: bool = True
    sl_min_pct: str = "1.2"
    atr_limit_mult: str = "0.5"
    tp1_pct: str = "3.5"
    mr_stretch_atr: str = "1.5"
    mr_stop_atr: str = "1.2"
    bo_vol_ratio: str = "2.5"
    bo_stop_atr: str = "1.0"
    bo_tp_atr: str = "2.0"
    breakeven_pct: str = "2.0"
    momentum_ttl_bars: int = 16
    momentum_time_stop_bars: int = 32
    mr_ttl_bars: int = 3
    mr_time_stop_bars: int = 8
    bo_ttl_bars: int = 2
    bo_time_stop_bars: int = 6
    circuit_pause_usdt: str = "30"
    circuit_stop_usdt: str = "40"
    circuit_pause_bars: int = 24
    min_notional_usdt: str = "5"


class RuneclawConfluenceStrategy(Strategy):
    def __init__(self, config: RuneclawConfluenceStrategyConfig) -> None:
        super().__init__(config)
        self.cfg = config
        self._rc_bnb_id: Optional[InstrumentId] = None
        self._rc_btc_id: Optional[InstrumentId] = None
        self._rc_bnb: Optional[Instrument] = None
        self._rc_h: dict = {k: deque(maxlen=_BUF) for k in ("h", "l", "c", "v")}
        self._rc_btc_h: dict = {k: deque(maxlen=_BUF) for k in ("h", "l", "c", "v")}
        self._rc_btc_feat = None
        self._rc_phase = "FLAT"          # FLAT | PENDING | LONG | SHORT
        self._rc_entry_order = None
        self._rc_stop_order = None
        self._rc_tp_order = None
        self._rc_pending_age = 0
        self._rc_pos_age = 0
        self._rc_plan: Optional[dict] = None
        self._rc_be_done = False
        self._rc_day = -1
        self._rc_day_pnl = 0.0
        self._rc_day_unreadable = False
        self._rc_pause_until_bar = 0
        self._rc_day_stopped = False
        self._rc_bar_index = 0
        self._rc_entries = {"momentum": 0, "mr": 0, "breakout": 0}
        self._rc_blocks = {"warmup": 0, "gate": 0, "chop": 0, "score": 0,
                           "rr": 0, "sizing": 0, "circuit": 0}

    # ------------------------------------------------------------------ setup
    def on_start(self) -> None:
        for iid in self.cfg.instrument_ids:
            text = str(iid)
            if "BNBUSDT" in text:
                self._rc_bnb_id = iid
            elif "BTCUSDT" in text:
                self._rc_btc_id = iid
        if self._rc_bnb_id is None and self.cfg.instrument_id is not None:
            self._rc_bnb_id = self.cfg.instrument_id
        if self._rc_bnb_id is None:
            raise RuntimeError("BNBUSDT instrument not found in spec")
        self._rc_bnb = self.cache.instrument(self._rc_bnb_id)
        for bar_type in self.cfg.bar_types:
            self.subscribe_bars(bar_type)
        if not self.cfg.bar_types and self.cfg.bar_type is not None:
            self.subscribe_bars(self.cfg.bar_type)

    # ------------------------------------------------------------------- bars
    def on_bar(self, bar: Bar) -> None:
        iid = bar.bar_type.instrument_id
        h, lo, c, v = float(bar.high), float(bar.low), float(bar.close), float(bar.volume)

        if self._rc_btc_id is not None and iid == self._rc_btc_id:
            for key, val in (("h", h), ("l", lo), ("c", c), ("v", v)):
                self._rc_btc_h[key].append(val)
            if len(self._rc_btc_h["c"]) >= _WINDOW + 1:
                self._rc_btc_feat = compute_features(
                    list(self._rc_btc_h["h"]), list(self._rc_btc_h["l"]),
                    list(self._rc_btc_h["c"]), list(self._rc_btc_h["v"]), _WINDOW)
            return

        if iid != self._rc_bnb_id:
            return

        self._rc_bar_index += 1
        for key, val in (("h", h), ("l", lo), ("c", c), ("v", v)):
            self._rc_h[key].append(val)
        self._rc_roll_day(bar)

        if self._rc_phase in ("LONG", "SHORT"):
            self._rc_manage_position(c)
            return
        if self._rc_phase == "PENDING":
            self._rc_pending_age += 1
            ttl = int((self._rc_plan or {}).get("ttl", 16))
            if self._rc_pending_age > ttl:
                self._rc_cancel_entry()
            return

        self._rc_reconcile()
        if self._rc_phase != "FLAT":
            return
        self._rc_try_enter(c)

    # ------------------------------------------------------- entry evaluation
    def _rc_try_enter(self, close: float) -> None:
        if self._rc_day_stopped or self._rc_bar_index < self._rc_pause_until_bar:
            self._rc_blocks["circuit"] += 1
            return
        if len(self._rc_h["c"]) < _WINDOW + 1 or self._rc_btc_feat is None:
            self._rc_blocks["warmup"] += 1
            return
        feats = compute_features(list(self._rc_h["h"]), list(self._rc_h["l"]),
                                 list(self._rc_h["c"]), list(self._rc_h["v"]), _WINDOW)
        if not feats.ok or feats.ema20_1h is None or feats.ema20_4h is None:
            self._rc_blocks["warmup"] += 1
            return

        cfgd = self._rc_cfg_dict()
        regime = classify_regime(self._rc_btc_feat, cfgd)
        state = regime["state"]
        if state == "CHOP":
            self._rc_blocks["chop"] += 1
            return

        allow_short = bool(self.cfg.allow_short)
        min_score = float(self.cfg.min_score)
        plan = None

        # 1) volume-spike breakout overlay (trend direction only; both in range)
        vol_ok = feats.vol_ratio is not None and feats.vol_ratio >= float(self.cfg.bo_vol_ratio)
        if vol_ok and len(self._rc_h["h"]) >= _WINDOW + 1:
            prior_hi = max(list(self._rc_h["h"])[-_WINDOW - 1:-1])
            prior_lo = min(list(self._rc_h["l"])[-_WINDOW - 1:-1])
            sides = []
            if state == "TREND_UP":
                sides = ["long"]
            elif state == "TREND_DOWN":
                sides = ["short"] if allow_short else []
            elif state == "RANGE":
                sides = ["long"] + (["short"] if allow_short else [])
            for side in sides:
                broke = close > prior_hi if side == "long" else close < prior_lo
                if not broke:
                    continue
                score, _ = confluence_score(feats, side, "momentum")
                if score < min_score:
                    self._rc_blocks["score"] += 1
                    continue
                plan = self._rc_build_plan(feats, side, "breakout", close)
                break

        # 2) momentum pullback in the trend direction
        if plan is None and state in ("TREND_UP", "TREND_DOWN"):
            side = "long" if state == "TREND_UP" else "short"
            if side == "short" and not allow_short:
                self._rc_blocks["gate"] += 1
            else:
                score, _ = confluence_score(feats, side, "momentum")
                if score >= min_score:
                    plan = self._rc_build_plan(feats, side, "momentum", close)
                else:
                    self._rc_blocks["score"] += 1

        # 3) average-price reversion in ranges
        if plan is None and state == "RANGE" and feats.atr and feats.vwap:
            stretch = (close - feats.vwap) / feats.atr
            lim = float(self.cfg.mr_stretch_atr)
            side = None
            if stretch <= -lim:
                side = "long"
            elif stretch >= lim and allow_short:
                side = "short"
            if side is not None:
                score, _ = confluence_score(feats, side, "mr")
                if score >= min_score:
                    plan = self._rc_build_plan(feats, side, "mr", close)
                else:
                    self._rc_blocks["score"] += 1

        if plan is None:
            return
        self._rc_submit_entry(plan)

    def _rc_build_plan(self, feats, side: str, kind: str, close: float) -> Optional[dict]:
        atr_v = feats.atr or 0.0
        vwap = feats.vwap or close
        sl_min = float(self.cfg.sl_min_pct) / 100.0
        long_side = side == "long"

        if kind == "momentum":
            entry = vwap - float(self.cfg.atr_limit_mult) * atr_v if long_side \
                else vwap + float(self.cfg.atr_limit_mult) * atr_v
            guard = feats.lo if long_side else feats.hi
            raw = (entry - guard) / entry if long_side else (guard - entry) / entry
            sl_pct = max(raw, sl_min)
            tp_pct = float(self.cfg.tp1_pct) / 100.0
            if tp_pct < 0.9 * sl_pct:
                self._rc_blocks["rr"] += 1
                return None
            ttl, tstop = int(self.cfg.momentum_ttl_bars), int(self.cfg.momentum_time_stop_bars)
        elif kind == "breakout":
            entry = close
            sl_pct = max(float(self.cfg.bo_stop_atr) * atr_v / entry, sl_min)
            tp_pct = max(float(self.cfg.bo_tp_atr) * atr_v / entry, sl_pct)
            ttl, tstop = int(self.cfg.bo_ttl_bars), int(self.cfg.bo_time_stop_bars)
        else:  # mr
            entry = close - 0.15 * atr_v if long_side else close + 0.15 * atr_v
            sl_pct = max(float(self.cfg.mr_stop_atr) * atr_v / entry, sl_min)
            tp_dist = (vwap - entry) if long_side else (entry - vwap)
            if tp_dist < sl_pct * entry:
                self._rc_blocks["rr"] += 1
                return None
            tp_pct = tp_dist / entry
            ttl, tstop = int(self.cfg.mr_ttl_bars), int(self.cfg.mr_time_stop_bars)

        if entry <= 0 or sl_pct <= 0:
            return None
        stop = entry * (1.0 - sl_pct) if long_side else entry * (1.0 + sl_pct)
        target = entry * (1.0 + tp_pct) if long_side else entry * (1.0 - tp_pct)
        ordered = (stop < entry < target) if long_side else (target < entry < stop)
        if not ordered:
            return None
        return {"side": side, "kind": kind, "entry": entry, "stop": stop,
                "target": target, "sl_pct": sl_pct, "ttl": ttl, "time_stop": tstop}

    # --------------------------------------------------------------- ordering
    def _rc_submit_entry(self, plan: dict) -> None:
        instrument = self._rc_bnb
        if instrument is None:
            return
        max_loss = float(self.cfg.max_loss_usdt)
        leverage = max(int(self.cfg.leverage), 1)
        budget = float(self.cfg.margin_budget)
        notional = max_loss / plan["sl_pct"]
        if budget > 0 and (notional / leverage) > budget:
            notional = budget * leverage
        qty = self._rc_qty_floor(notional / plan["entry"], instrument)
        if qty is None or float(qty) * plan["entry"] < float(self.cfg.min_notional_usdt):
            self._rc_blocks["sizing"] += 1
            return
        order = self.order_factory.limit(
            instrument_id=instrument.id,
            order_side=OrderSide.BUY if plan["side"] == "long" else OrderSide.SELL,
            quantity=qty,
            price=self._rc_px(plan["entry"], instrument),
            time_in_force=TimeInForce.GTC,
        )
        self._rc_entry_order = order
        self._rc_plan = plan
        self._rc_pending_age = 0
        self._rc_phase = "PENDING"
        self._rc_entries[plan["kind"]] += 1
        self.submit_order(order)

    def on_position_opened(self, event) -> None:
        self._rc_phase = "LONG" if (self._rc_plan or {}).get("side") == "long" else "SHORT"
        self._rc_entry_order = None
        self._rc_pos_age = 0
        self._rc_be_done = False
        instrument = self._rc_bnb
        plan = self._rc_plan
        if instrument is None or plan is None:
            return
        qty = None
        for position in self.cache.positions_open(instrument_id=instrument.id):
            qty = position.quantity
        if qty is None:
            return
        exit_side = OrderSide.SELL if plan["side"] == "long" else OrderSide.BUY
        try:
            stop_order = self.order_factory.stop_market(
                instrument_id=instrument.id,
                order_side=exit_side,
                quantity=qty,
                trigger_price=self._rc_px(plan["stop"], instrument),
                time_in_force=TimeInForce.GTC,
            )
            self._rc_stop_order = stop_order
            self.submit_order(stop_order)
        except Exception as exc:  # surfaced in run logs; position still guarded by bar check
            print(f"[runeclaw-confluence] stop order rejected: {type(exc).__name__}: {exc}")
            self._rc_stop_order = None
        try:
            tp_order = self.order_factory.limit(
                instrument_id=instrument.id,
                order_side=exit_side,
                quantity=qty,
                price=self._rc_px(plan["target"], instrument),
                time_in_force=TimeInForce.GTC,
            )
            self._rc_tp_order = tp_order
            self.submit_order(tp_order)
        except Exception as exc:
            print(f"[runeclaw-confluence] tp order rejected: {type(exc).__name__}: {exc}")
            self._rc_tp_order = None

    def _rc_manage_position(self, close: float) -> None:
        plan = self._rc_plan or {}
        self._rc_pos_age += 1
        long_side = self._rc_phase == "LONG"

        # Backstop only when the resting stop could not be placed: exit at
        # market on a stop breach detected from the bar.
        if self._rc_stop_order is None:
            breached = close <= plan.get("stop", 0.0) if long_side \
                else close >= plan.get("stop", float("inf"))
            if breached:
                self._rc_flatten("stop_backstop")
                return

        # Breakeven lift once the move covers the configured cushion.
        be_pct = float(self.cfg.breakeven_pct) / 100.0
        entry = plan.get("entry", 0.0)
        if (not self._rc_be_done and entry > 0 and self._rc_stop_order is not None):
            moved = (close / entry - 1.0) if long_side else (1.0 - close / entry)
            if moved >= be_pct:
                try:
                    self.cancel_order(self._rc_stop_order)
                    instrument = self._rc_bnb
                    qty = None
                    for position in self.cache.positions_open(instrument_id=instrument.id):
                        qty = position.quantity
                    if qty is not None:
                        be_px = entry * (1.001 if long_side else 0.999)
                        stop_order = self.order_factory.stop_market(
                            instrument_id=instrument.id,
                            order_side=OrderSide.SELL if long_side else OrderSide.BUY,
                            quantity=qty,
                            trigger_price=self._rc_px(be_px, instrument),
                            time_in_force=TimeInForce.GTC,
                        )
                        self._rc_stop_order = stop_order
                        self.submit_order(stop_order)
                        self._rc_be_done = True
                except Exception as exc:
                    print("[runeclaw-confluence] breakeven lift failed: "
                          f"{type(exc).__name__}: {exc}")

        if self._rc_pos_age > int(plan.get("time_stop", 32)):
            self._rc_flatten("time_stop")

    def on_position_closed(self, event) -> None:
        try:
            position = self.cache.position(event.position_id)
            realized = getattr(position, "realized_pnl", None)
            self._rc_day_pnl += float(realized.as_double() if hasattr(realized, "as_double")
                                      else realized)
        except Exception:
            if not self._rc_day_unreadable:
                print("[runeclaw-confluence] realized PnL unreadable; circuit breaker "
                      "disabled for this run (never counted as zero)")
            self._rc_day_unreadable = True
        self._rc_after_flat()
        if not self._rc_day_unreadable:
            pause_at = -abs(float(self.cfg.circuit_pause_usdt))
            stop_at = -abs(float(self.cfg.circuit_stop_usdt))
            if self._rc_day_pnl <= stop_at:
                self._rc_day_stopped = True
            elif self._rc_day_pnl <= pause_at:
                self._rc_pause_until_bar = self._rc_bar_index + int(self.cfg.circuit_pause_bars)

    # ------------------------------------------------------------- housekeeping
    def _rc_after_flat(self) -> None:
        instrument = self._rc_bnb
        if instrument is not None:
            try:
                self.cancel_all_orders(instrument.id)
            except Exception:
                pass
        self._rc_phase = "FLAT"
        self._rc_entry_order = None
        self._rc_stop_order = None
        self._rc_tp_order = None
        self._rc_plan = None
        self._rc_pos_age = 0

    def _rc_cancel_entry(self) -> None:
        if self._rc_entry_order is not None:
            try:
                self.cancel_order(self._rc_entry_order)
            except Exception:
                pass
        self._rc_after_flat()

    def _rc_flatten(self, reason: str) -> None:
        instrument = self._rc_bnb
        if instrument is None:
            return
        try:
            self.cancel_all_orders(instrument.id)
        except Exception:
            pass
        for position in self.cache.positions_open(instrument_id=instrument.id):
            order = self.order_factory.market(
                instrument_id=instrument.id,
                order_side=OrderSide.SELL if position.is_long else OrderSide.BUY,
                quantity=position.quantity,
                time_in_force=TimeInForce.GTC,
            )
            self.submit_order(order)

    def _rc_reconcile(self) -> None:
        """Positions-vs-state honesty: a position the FSM does not know about is
        flattened rather than adopted."""
        instrument = self._rc_bnb
        if instrument is None or self._rc_phase != "FLAT":
            return
        open_positions = list(self.cache.positions_open(instrument_id=instrument.id))
        if open_positions:
            print(f"[runeclaw-confluence] reconcile: {len(open_positions)} unexpected "
                  "open position(s); flattening")
            self._rc_flatten("reconcile")

    def _rc_roll_day(self, bar: Bar) -> None:
        day = int(bar.ts_event // _NS_PER_DAY)
        if day != self._rc_day:
            self._rc_day = day
            self._rc_day_pnl = 0.0
            self._rc_day_stopped = False

    def _rc_cfg_dict(self) -> dict:
        return {"regime_trend_min_pct": "1.5", "regime_high_vol_atrp": "0.03"}

    # ------------------------------------------------------------------ utils
    def _rc_qty_floor(self, value: float, instrument: Instrument) -> Optional[Quantity]:
        try:
            step = Decimal(10) ** -int(instrument.size_precision)
            floored = Decimal(str(value)).quantize(step, rounding=ROUND_DOWN)
            qty = Quantity(floored, instrument.size_precision)
        except Exception:
            return None
        return qty if float(qty) > 0 else None

    def _rc_px(self, value: float, instrument: Instrument) -> Price:
        return Price(Decimal(str(round(value, instrument.price_precision))),
                     instrument.price_precision)

    def on_stop(self) -> None:
        print(f"[runeclaw-confluence] entries={self._rc_entries} blocks={self._rc_blocks} "
              f"day_pnl_unreadable={self._rc_day_unreadable}")
        if self._rc_bnb is not None:
            self.cancel_all_orders(self._rc_bnb.id)
            self.close_all_positions(self._rc_bnb.id)
