"""
RUNECLAW Partial Take-Profit Ladder — institutional-style position scaling.

Instead of all-or-nothing exits at a single TP, this module manages
staged profit-taking:
  Stage 1 (TP1): Close 50% at 1.5R, move SL to breakeven
  Stage 2 (TP2): Close 30% at 2.5R, lock profit with tight trail
  Stage 3 (Runner): Remaining 20% rides with aggressive ATR trail

Benefits:
  - Locks in profits early to reduce variance
  - Lets winners run with a portion of the position
  - Moves SL to breakeven after TP1, making the trade "risk-free"
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, fields
from typing import Optional

from bot.config import CONFIG

logger = logging.getLogger(__name__)


@dataclass
class PartialTPState:
    """Tracks partial TP progress for a single position."""
    trade_id: str
    direction: str  # "LONG" or "SHORT"
    entry_price: float
    original_sl: float
    original_tp: float
    initial_risk: float  # abs(entry - sl)
    original_qty: float
    atr: float  # ATR at entry for trailing

    # Stage tracking
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp1_qty_closed: float = 0.0
    tp2_qty_closed: float = 0.0
    remaining_qty: float = 0.0
    current_sl: float = 0.0
    runner_trail_best: float = 0.0
    #: A stage whose close order went out and whose fill could not be read:
    #: ``{"stage", "order_id", "qty", "new_sl"}``. While it is set the ladder
    #: does nothing but re-read that order, because resubmitting a close whose
    #: first order may already have filled closes the position twice, and
    #: acting on the next stage would build on a fill nobody confirmed.
    pending: Optional[dict] = None

    def __post_init__(self):
        # A NEW ladder starts here. A saved one is restored by `from_record`,
        # which puts these three back after this has run.
        self.remaining_qty = self.original_qty
        self.current_sl = self.original_sl
        self.runner_trail_best = self.entry_price

    #: What a stage leaves behind that construction would otherwise reset.
    _RECORDED = ("remaining_qty", "current_sl", "runner_trail_best")

    @classmethod
    def from_record(cls, record: dict) -> "PartialTPState":
        """The ladder a saved record describes, including what it had done.

        The executor saves the ladder after every pass and reads it back at
        the start of the next, so a restart is not the only reload: every
        tick is one. Rebuilding it through the constructor ran `__post_init__`
        over the saved dict and reset the runner's best price to the entry,
        its stop to the entry-time stop and its remaining quantity to the
        original, so the runner trailed from the current price rather than the
        best one, and a move the venue refused at the peak was retried from
        wherever the price had fallen to. Keys this build does not know are
        dropped; a recorded value that is not a finite number is not restored.
        """
        known = {f.name for f in fields(cls)}
        st = cls(**{k: v for k, v in record.items() if k in known})
        for name in cls._RECORDED:
            v = record.get(name)
            if (isinstance(v, (int, float)) and not isinstance(v, bool)
                    and math.isfinite(v)):
                setattr(st, name, float(v))
        return st


@dataclass
class PartialTPAction:
    """Action to execute from partial TP check."""
    action: str  # "close_partial", "move_sl", "close_runner", "none"
    qty_to_close: float = 0.0
    new_sl: float = 0.0
    reason: str = ""
    stage: str = ""  # "tp1", "tp2", "runner_sl"


def create_partial_tp_state(
    trade_id: str,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    quantity: float,
    atr: float,
) -> PartialTPState:
    """Create a new partial TP tracking state for a position."""
    initial_risk = abs(entry_price - stop_loss)
    return PartialTPState(
        trade_id=trade_id,
        direction=direction,
        entry_price=entry_price,
        original_sl=stop_loss,
        original_tp=take_profit,
        initial_risk=initial_risk,
        original_qty=quantity,
        atr=atr if atr > 0 else entry_price * 0.02,
    )


def rebuild_ladder(*, trade_id: str, direction: str, entry_price: float,
                   stop_loss: float, take_profit: float, quantity: float,
                   atr: float, entry_risk: object,
                   restored_without_ladder: bool) -> tuple[Optional[PartialTPState], str]:
    """A ladder for a position that has none, or the reason it cannot have one.

    THE 1R IS THE ENTRY-TIME RISK, NEVER THE DISTANCE TO A STOP THAT HAS
    MOVED. The ladder is denominated in R, and a stop the trailing path or TP1
    has already pulled toward entry sits a sliver from it: measured from
    there, 1R is ~0.1% of price, every tick reads as many R, and TP1 and TP2
    fire at once on whatever is left. That is what a restart did, because the
    ladder was never written to disk. `entry_risk` is the trailing state's own
    record of the 1R taken at the fill; without it, the distance to the stop is
    the 1R only while the stop still sits on the LOSS side of entry, which is
    the one arrangement where nothing can have moved it yet.

    `restored_without_ladder` is a position loaded from a record written before
    the ladder was persisted, so whether TP1 or TP2 already fired is not on
    record. The stop is the evidence: TP1 moves it to breakeven and TP2 to 1R
    in profit, so a stop already there marks those stages done. Reading a
    trailed stop the same way can skip a TP1 that never fired, and that is the
    direction to be wrong in -- a missed partial close keeps a position the
    stop still protects, and a repeated one sells a runner twice.
    """
    is_long = direction == "LONG"
    risk = (float(entry_risk) if isinstance(entry_risk, (int, float))
            and not isinstance(entry_risk, bool) and math.isfinite(entry_risk)
            and entry_risk > 0 else None)
    if risk is None:
        on_loss_side = stop_loss > 0 and (stop_loss < entry_price if is_long
                                          else stop_loss > entry_price)
        if not on_loss_side:
            return None, ("its entry-time 1R was not recorded and its stop no longer "
                          "sits on the loss side of entry, so no R can be measured")
        risk = abs(entry_price - stop_loss)
    entry_stop = entry_price - risk if is_long else entry_price + risk
    st = create_partial_tp_state(
        trade_id=trade_id, direction=direction, entry_price=entry_price,
        stop_loss=entry_stop, take_profit=take_profit, quantity=quantity, atr=atr)
    st.current_sl = stop_loss
    st.remaining_qty = quantity
    if restored_without_ladder and stop_loss > 0:
        st.tp1_hit = stop_loss >= entry_price if is_long else stop_loss <= entry_price
        st.tp2_hit = (stop_loss >= entry_price + risk if is_long
                      else stop_loss <= entry_price - risk)
    return st, ""


def _tp1_lock(state: PartialTPState) -> float:
    """TP1's stop: breakeven, plus a small buffer for fees."""
    fee_buffer = state.entry_price * 0.001  # 0.1% buffer
    return (state.entry_price + fee_buffer if state.direction == "LONG"
            else state.entry_price - fee_buffer)


def _tp2_lock(state: PartialTPState) -> float:
    """TP2's stop: 1R of profit locked in."""
    return (state.entry_price + state.initial_risk if state.direction == "LONG"
            else state.entry_price - state.initial_risk)


def stage_lock(state: PartialTPState) -> Optional[float]:
    """The stop the stages that have fired lock in, or None before TP1.

    A stage fires once, so the stop move it asks for was asked for once: when
    the venue refused it, nothing asked again, and a position whose TP1 had
    closed half of it kept its original stop on the other half. This is the
    level `check_partial_tp` proposes on every pass until the stop reaches it.
    """
    if state.tp2_hit:
        return _tp2_lock(state)
    if state.tp1_hit:
        return _tp1_lock(state)
    return None


def check_partial_tp(
    state: PartialTPState,
    current_price: float,
) -> list[PartialTPAction]:
    """Check if current price triggers any partial TP actions.

    Returns a list of actions to execute (may be multiple if price
    jumped past multiple levels in one tick).
    """
    if not CONFIG.partial_tp.enabled:
        return []

    actions: list[PartialTPAction] = []
    cfg = CONFIG.partial_tp

    is_long = state.direction == "LONG"

    # Calculate R-multiple of current price move
    if is_long:
        current_r = (current_price - state.entry_price) / state.initial_risk if state.initial_risk > 0 else 0
    else:
        current_r = (state.entry_price - current_price) / state.initial_risk if state.initial_risk > 0 else 0

    # Check TP1: first partial close
    if not state.tp1_hit and current_r >= cfg.tp1_r_multiple:
        close_qty = state.original_qty * (cfg.tp1_close_pct / 100.0)
        close_qty = min(close_qty, state.remaining_qty)

        if close_qty > 0:
            state.tp1_hit = True
            state.tp1_qty_closed = close_qty
            state.remaining_qty -= close_qty

            # Move SL to breakeven (+ small buffer for fees)
            new_sl = _tp1_lock(state)
            state.current_sl = new_sl

            actions.append(PartialTPAction(
                action="close_partial",
                qty_to_close=close_qty,
                new_sl=new_sl,
                reason=f"TP1 hit at {current_r:.1f}R — closing {cfg.tp1_close_pct:.0f}%, SL→breakeven",
                stage="tp1",
            ))

    # Check TP2: second partial close
    if state.tp1_hit and not state.tp2_hit and current_r >= cfg.tp2_r_multiple:
        close_qty = state.original_qty * (cfg.tp2_close_pct / 100.0)
        close_qty = min(close_qty, state.remaining_qty)

        if close_qty > 0:
            state.tp2_hit = True
            state.tp2_qty_closed = close_qty
            state.remaining_qty -= close_qty

            # Tighten SL to lock profit (1R above entry)
            new_sl = _tp2_lock(state)
            state.current_sl = new_sl

            actions.append(PartialTPAction(
                action="close_partial",
                qty_to_close=close_qty,
                new_sl=new_sl,
                reason=f"TP2 hit at {current_r:.1f}R — closing {cfg.tp2_close_pct:.0f}%, locking 1R profit",
                stage="tp2",
            ))

    # A stage's lock the stop has not reached is asked for again. When the
    # stage fired on this call, current_sl already sits at its lock and this
    # asks nothing. The runner builds on the lock rather than moving twice.
    lock = stage_lock(state)
    if lock is not None and not (lock > state.current_sl if is_long
                                 else lock < state.current_sl):
        lock = None                  # the stop already holds it

    # Runner: aggressive trailing stop for remaining position
    if state.tp2_hit and state.remaining_qty > 0:
        trail_dist = state.atr * cfg.runner_trail_atr_mult
        floor = lock if lock is not None else state.current_sl

        if is_long:
            if current_price > state.runner_trail_best:
                state.runner_trail_best = current_price
            trail_sl = state.runner_trail_best - trail_dist
            new_sl = max(trail_sl, floor)  # never lower SL

            if new_sl > state.current_sl:
                state.current_sl = new_sl
                actions.append(PartialTPAction(
                    action="move_sl",
                    new_sl=new_sl,
                    reason=f"Runner trail: SL→{new_sl:.4f} (best={state.runner_trail_best:.4f})",
                    stage="runner",
                ))

            # Check if runner SL hit
            if current_price <= state.current_sl:
                actions.append(PartialTPAction(
                    action="close_runner",
                    qty_to_close=state.remaining_qty,
                    reason=f"Runner SL hit at {current_price:.4f}",
                    stage="runner_sl",
                ))
        else:
            if current_price < state.runner_trail_best:
                state.runner_trail_best = current_price
            trail_sl = state.runner_trail_best + trail_dist
            new_sl = min(trail_sl, floor)  # never raise SL for shorts

            if new_sl < state.current_sl:
                state.current_sl = new_sl
                actions.append(PartialTPAction(
                    action="move_sl",
                    new_sl=new_sl,
                    reason=f"Runner trail: SL→{new_sl:.4f} (best={state.runner_trail_best:.4f})",
                    stage="runner",
                ))

            # Check if runner SL hit
            if current_price >= state.current_sl:
                actions.append(PartialTPAction(
                    action="close_runner",
                    qty_to_close=state.remaining_qty,
                    reason=f"Runner SL hit at {current_price:.4f}",
                    stage="runner_sl",
                ))
    elif lock is not None:
        state.current_sl = lock
        actions.append(PartialTPAction(
            action="move_sl",
            new_sl=lock,
            reason=f"Stage lock not yet on the stop: SL→{lock:.4f}",
            stage="lock",
        ))

    return actions


def partial_tp_summary(state: PartialTPState) -> dict:
    """Get a summary of partial TP state for display."""
    pct_closed = ((state.tp1_qty_closed + state.tp2_qty_closed) / state.original_qty * 100) if state.original_qty > 0 else 0
    pct_remaining = (state.remaining_qty / state.original_qty * 100) if state.original_qty > 0 else 0

    return {
        "trade_id": state.trade_id,
        "tp1_hit": state.tp1_hit,
        "tp2_hit": state.tp2_hit,
        "pct_closed": round(pct_closed, 1),
        "pct_remaining": round(pct_remaining, 1),
        "current_sl": state.current_sl,
        "original_sl": state.original_sl,
        "runner_best": state.runner_trail_best if state.tp2_hit else None,
    }
