#!/usr/bin/env python3
r"""
RUNECLAW v8 - Targeted Training Data Generator
================================================
Synthesizes the samples the 45-prompt eval proved are missing. The v8
curriculum comes from measured failures of BOTH the v7 and June models on
the clean harness (2026-08-13):

1. STATE GATES  - v7 approved through all five account/calendar/data gates;
   both models missed daily-loss and macro-event. Every rejection here cites
   the SPECIFIC failed check - never "all 23 checks passed" (v7 fabricated
   exactly that on a tripped circuit breaker).
2. R:R ARITHMETIC - both models scored ~54%: stated ratios disagreeing with
   their own numbers, and both approved the 1.17 tight-TP trap. Here the
   stated ratio ALWAYS equals the computed one, and sub-1.2 always rejects.
3. REGIME SIZING - both scored 33%: regimes name-dropped, multipliers never
   applied. Here the multiplication is written out every time.
4. CONFIDENCE - always a 0-1 decimal, never a percent or a word.
5. BALANCE - approve-side controls at ~1:1 with rejections, because June is
   the proof of what unbalanced refusal training does: it rejected textbook
   longs and shorts. Teaching "no" requires teaching "yes" in the same breath.
6. NO-DATA REFUSAL + FULL REPORT - bare "BTC" prompts in ad-hoc chat produced
   fully fabricated GETCLAW tables (invented prices, invented voters, a
   "19/23 passed -> APPROVED" verdict under a fail-closed policy). The fix is
   a pair: refuse in-format when no data is given, AND produce the full
   sectioned report - with the TRAINED 12-voter table and the confluence /
   confidence arithmetic written out - when the payload IS given.

Instance-level test leakage is avoided (different symbols/numbers/phrasing
than eval_prompts_v2.json); category-level overlap is the point - the eval
measures whether the CAPABILITY was learned, and the live shadow A/B stays
the truly held-out judge.

Deterministic: same seed -> byte-identical output. Stdlib only.

Usage:
  python generate_v8_training_data.py                      # 12,000 samples
  python generate_v8_training_data.py --count 6000 --seed 7

Output:
  training_data/v8_targeted.jsonl
  training_data/V8_GENERATION_MANIFEST.json

Then:
  python curate_training_data.py --output training_data\curated_v8_final.jsonl ^
      --input training_data\v8_targeted.jsonl training_data\curated_v8_all.jsonl
  python train_runeclaw_v7_8b.py --data training_data\curated_v8_final.jsonl --run-name v8
"""

import argparse
import hashlib
import json
import os
import random

SYMBOLS = [
    # (pair, base price, decimals, meme?)
    ("BTC/USDT", 67000, 0, False), ("ETH/USDT", 3400, 0, False),
    ("SOL/USDT", 150, 2, False), ("BNB/USDT", 590, 2, False),
    ("XRP/USDT", 2.2, 4, False), ("ADA/USDT", 0.46, 4, False),
    ("LINK/USDT", 14.8, 2, False), ("AVAX/USDT", 27.8, 2, False),
    ("DOT/USDT", 6.4, 3, False), ("NEAR/USDT", 5.4, 3, False),
    ("LTC/USDT", 85, 2, False), ("ATOM/USDT", 7.9, 3, False),
    ("DOGE/USDT", 0.118, 4, True), ("PEPE/USDT", 0.0000125, 7, True),
    ("SHIB/USDT", 0.000022, 7, True), ("WIF/USDT", 1.85, 3, True),
]
TIMEFRAMES = ["5m", "15m", "1H", "4H"]
SECTORS = {"Layer-1": ["SOL", "AVAX", "NEAR", "ADA", "DOT"],
           "meme": ["DOGE", "PEPE", "SHIB", "WIF"]}

rng = random.Random()


def fmt(price, decimals):
    return f"{price:,.{decimals}f}" if decimals else f"{price:,.0f}"


def pick_symbol(meme=None):
    pool = [s for s in SYMBOLS if meme is None or s[3] == meme]
    return rng.choice(pool)


def bullish_payload(price, decimals, atr_pct):
    """A GOOD long setup — the temptation the state gates must overrule."""
    return (f"RSI-14: {rng.randint(26, 34)} (oversold). MACD histogram: turning positive. "
            f"Bollinger %B: 0.{rng.randint(5, 14):02d}. Volume: {rng.uniform(1.8, 3.0):.1f}x average "
            f"aligned with the bounce. EMA9 crossing above EMA21. OBV rising. "
            f"Price: {fmt(price, decimals)}. ATR: {atr_pct:.1f}% of price.")


def bearish_payload(price, decimals, atr_pct):
    return (f"RSI-14: {rng.randint(72, 79)} (overbought). MACD histogram: negative and expanding. "
            f"Bollinger %B: 0.{rng.randint(86, 95)}. EMA9 below EMA21. OBV falling. "
            f"Price rejecting at {fmt(price, decimals)} on {rng.uniform(1.6, 2.4):.1f}x volume. "
            f"ATR: {atr_pct:.1f}% of price.")


def trade_numbers(price, decimals, direction, rr_target):
    """Entry/SL/TP whose computed R:R equals rr_target (2dp)."""
    risk_pct = rng.uniform(0.018, 0.032)
    risk = price * risk_pct
    reward = risk * rr_target
    if direction == "LONG":
        sl, tp = price - risk, price + reward
    else:
        sl, tp = price + risk, price - reward
    sl, tp = round(sl, decimals), round(tp, decimals)
    if direction == "LONG":
        computed = (tp - price) / (price - sl)
    else:
        computed = (price - tp) / (sl - price)
    return sl, tp, round(computed, 2)


def approve_block(sym, tf, direction, price, sl, tp, rr, conf, extra=""):
    decimals = next(s[2] for s in SYMBOLS if s[0] == sym)
    return (f"TRADE IDEA\n"
            f"Direction: {direction}\n"
            f"Asset: {sym}\n"
            f"Timeframe: {tf}\n"
            f"Entry: {fmt(price, decimals)}\n"
            f"Stop Loss: {fmt(sl, decimals)}\n"
            f"Take Profit: {fmt(tp, decimals)}\n"
            f"Risk:Reward: 1:{rr:.2f}\n"
            f"Confidence: {conf:.2f}\n"
            f"{extra}"
            f"Risk Check: APPROVED\n"
            f"- RISK_REWARD: {rr:.2f} >= 1.2 minimum\n"
            f"- CONFIDENCE: {conf:.2f} >= 0.55 threshold\n")


# ── Scenario builders (each returns instruction, input, output) ──────────

def gen_state_gate_reject():
    sym, price0, decimals, _ = pick_symbol(meme=False)
    price = price0 * rng.uniform(0.85, 1.15)
    tf = rng.choice(TIMEFRAMES)
    atr = rng.uniform(1.4, 2.6)
    payload = bullish_payload(price, decimals, atr)
    conf = rng.uniform(0.60, 0.78)

    gates = {
        # (weight, check name, state line, failed-check detail)
        "DAILY_LOSS": (3, f"ACCOUNT STATE: today's realized P&L is -{rng.uniform(4.2, 4.9):.1f}% of equity "
                          f"(daily loss limit 5%); this trade would risk a further {rng.uniform(1.0, 2.0):.1f}%.",
                       "projected daily loss would breach the 5% limit"),
        "MACRO_EVENT": (3, f"CALENDAR: {rng.choice(['FOMC rate decision', 'CPI release', 'NFP print'])} "
                           f"in {rng.randint(15, 55)} minutes.",
                        "high-impact macro event imminent — spreads and slippage unquantifiable"),
        "CIRCUIT_BREAKER": (2, f"ACCOUNT STATE: drawdown from peak equity is {rng.uniform(10.1, 12.5):.1f}%.",
                            "system halted — drawdown exceeds the 10% circuit breaker"),
        "COOLDOWN": (2, f"ACCOUNT STATE: a stop loss on this symbol was hit {rng.randint(1, 4)} bars ago; "
                        f"the post-stop cooldown is 6 bars.",
                     "cooldown after the last stop loss has not elapsed"),
        "STALE_DATA": (2, f"DATA STATE: the most recent candle in the feed is {rng.randint(12, 40)} minutes "
                          f"old on a {tf} timeframe.",
                       "market data is stale — indicators reflect a price that no longer exists"),
        # Weight 3, not 1: the v8 model trained at weight 1 wrote
        # "PASS: POSITION_LIMIT: max 5 positions already open" on the eval —
        # the one gate it inverted was the one gate it barely saw.
        "OPEN_POSITIONS": (3, "ACCOUNT STATE: 5 positions already open (BTC, ETH, SOL, LINK, ATOM); "
                              "max open positions is 5.",
                           "position limit reached: 5 of 5 — a full book adds no new risk"),
        "CORRELATION": (1, "ACCOUNT STATE: already holding LONG SOL and LONG AVAX (both Layer-1); "
                           "max correlated positions per sector is 2.",
                        "sector correlation cap reached: 2 Layer-1 positions already open"),
        "LOSS_STREAK": (1, f"ACCOUNT STATE: 2 consecutive losing trades; minimum confidence after "
                           f"2 losses is 0.70. Confluence-implied confidence here is {conf:.2f}.",
                        f"confidence {conf:.2f} below the 0.70 post-streak requirement"),
    }
    name = rng.choices(list(gates), weights=[g[0] for g in gates.values()])[0]
    _w, state_line, detail = gates[name]
    if name == "LOSS_STREAK":
        conf = rng.uniform(0.56, 0.68)

    instruction = f"Analyze {sym} on the {tf} timeframe and run the RUNECLAW risk checks."
    inp = f"{payload}\n{state_line}"
    output = (f"Trade Scan: {sym}\n"
              f"Direction considered: LONG\n"
              f"Confidence: {conf:.2f}\n"
              f"Risk Check: REJECTED\n\n"
              f"Failed Checks:\n"
              f"- {name}: {detail}\n\n"
              f"Reasoning: The market setup itself is attractive — oversold RSI with positive "
              f"momentum and volume confirmation would normally justify a long. But the "
              f"{name} gate is binding, and no signal quality overrides a binding gate. "
              f"Only the checks that could be evaluated from the provided state are cited.\n\n"
              f"Action: NO TRADE. Capital preservation above all.")
    return instruction, inp, output


def gen_healthy_state_approve():
    """Same shape as the gate rejects, but the state is HEALTHY -> approve.
    This is the 1:1 balance that keeps v8 from learning blanket refusal."""
    sym, price0, decimals, _ = pick_symbol(meme=False)
    price = round(price0 * rng.uniform(0.85, 1.15), decimals)
    tf = rng.choice(TIMEFRAMES)
    direction = rng.choice(["LONG", "SHORT"])
    payload = bullish_payload(price, decimals, rng.uniform(1.4, 2.4)) if direction == "LONG" \
        else bearish_payload(price, decimals, rng.uniform(1.4, 2.4))
    state_line = (f"ACCOUNT STATE: drawdown {rng.uniform(0.5, 4.0):.1f}% (limit 10%), today's P&L "
                  f"{rng.uniform(-1.5, 1.5):+.1f}% (limit -5%), {rng.randint(0, 3)} of 5 positions open, "
                  f"no cooldown active. DATA STATE: feed current.")
    rr = round(rng.uniform(1.4, 3.2), 2)
    sl, tp, rr = trade_numbers(price, decimals, direction, rr)
    conf = rng.uniform(0.58, 0.84)
    instruction = f"Analyze {sym} on the {tf} timeframe and run the RUNECLAW risk checks."
    inp = f"{payload}\n{state_line}"
    extra = (f"Reasoning: Signal confluence supports the {direction.lower()}, and every gate that "
             f"the provided state allows checking is clear: drawdown and daily loss are inside "
             f"limits, position count has room, no cooldown is active, data is current.\n")
    output = approve_block(sym, tf, direction, price, sl, tp, rr, conf, extra)
    return instruction, inp, output


def gen_rr_reject():
    sym, price0, decimals, _ = pick_symbol(meme=False)
    price = round(price0 * rng.uniform(0.85, 1.15), decimals)
    tf = rng.choice(TIMEFRAMES)
    rr_bad = round(rng.uniform(0.95, 1.19), 2)
    sl, tp, rr_bad = trade_numbers(price, decimals, "LONG", rr_bad)
    risk = round(price - sl, decimals if decimals else 2)
    reward = round(tp - price, decimals if decimals else 2)
    instruction = f"Evaluate this proposed {sym} long on the {tf} against the RUNECLAW risk framework."
    inp = (f"RSI {rng.randint(30, 38)}, MACD histogram positive, volume {rng.uniform(1.5, 2.2):.1f}x. "
           f"Proposed trade: entry {fmt(price, decimals)}, stop loss {fmt(sl, decimals)}, "
           f"take profit {fmt(tp, decimals)}. Minimum risk:reward is 1.2.")
    output = (f"Trade Scan: {sym}\n"
              f"Direction considered: LONG\n"
              f"Risk Check: REJECTED\n\n"
              f"Failed Checks:\n"
              f"- RISK_REWARD: risk = {fmt(price, decimals)} - {fmt(sl, decimals)} = {risk}, "
              f"reward = {fmt(tp, decimals)} - {fmt(price, decimals)} = {reward}, "
              f"R:R = {rr_bad:.2f} < 1.2 minimum\n\n"
              f"Reasoning: The signals lean bullish, but the geometry does not pay: risking "
              f"{risk} to make {reward} is a {rr_bad:.2f} ratio, below the 1.2 floor. A good "
              f"direction with bad geometry is still a bad trade.\n\n"
              f"Action: NO TRADE. Widen the target or tighten the stop before reconsidering.")
    return instruction, inp, output


def gen_rr_approve():
    sym, price0, decimals, _ = pick_symbol(meme=False)
    price = round(price0 * rng.uniform(0.85, 1.15), decimals)
    tf = rng.choice(TIMEFRAMES)
    direction = rng.choice(["LONG", "SHORT"])
    rr = round(rng.uniform(1.30, 3.60), 2)
    sl, tp, rr = trade_numbers(price, decimals, direction, rr)
    conf = rng.uniform(0.58, 0.82)
    payload = bullish_payload(price, decimals, rng.uniform(1.4, 2.4)) if direction == "LONG" \
        else bearish_payload(price, decimals, rng.uniform(1.4, 2.4))
    instruction = f"Analyze {sym} on the {tf} timeframe. Generate a RUNECLAW TradeIdea."
    output = approve_block(sym, tf, direction, price, sl, tp, rr, conf,
                           f"Reasoning: Confluence supports the {direction.lower()} and the stated "
                           f"ratio is computed from the levels above, not asserted.\n")
    return instruction, payload, output


def gen_regime_sizing():
    regimes = {
        "CHOPPY": (0.5, "reduce"), "RANGING": (0.7, "slightly reduce"),
        "HIGH_VOLATILITY": (0.3, "heavily reduce"), "STRONG_TREND_UP": (1.5, "increase"),
    }
    regime = rng.choice(list(regimes))
    mult, verb = regimes[regime]
    sym, price0, decimals, _ = pick_symbol(meme=False)
    price = round(price0 * rng.uniform(0.85, 1.15), decimals)
    base = 2.0
    final = round(base * mult, 2)
    direction = "LONG"
    rr = round(rng.uniform(1.4, 2.8), 2)
    sl, tp, rr = trade_numbers(price, decimals, direction, rr)
    conf = rng.uniform(0.58, 0.80)
    reject = regime == "CHOPPY" and rng.random() < 0.4
    instruction = (f"Market regime: {regime}. Analyze {sym} on the 4H and apply the RUNECLAW "
                   f"regime rules including position sizing.")
    inp = bullish_payload(price, decimals, rng.uniform(1.6, 2.8))
    sizing = (f"Position Sizing:\n"
              f"- Base risk: {base:.1f}% of equity\n"
              f"- Regime multiplier: {mult}x ({regime} -> {verb} size)\n"
              f"- Final risk: {base:.1f}% x {mult} = {final}% of equity\n")
    if reject:
        output = (f"Trade Scan: {sym}\n"
                  f"Regime: {regime}\n"
                  f"Confidence: {rng.uniform(0.40, 0.54):.2f}\n"
                  f"Risk Check: REJECTED\n\n"
                  f"Failed Checks:\n"
                  f"- REGIME_FILTER: {regime} demands stronger confluence than this setup shows\n\n"
                  f"{sizing}"
                  f"Reasoning: Even at the reduced {final}% size the edge is not there in chop.\n"
                  f"Action: NO TRADE.")
    else:
        output = (f"TRADE IDEA\n"
                  f"Direction: {direction}\n"
                  f"Asset: {sym}\n"
                  f"Timeframe: 4H\n"
                  f"Regime: {regime}\n"
                  f"Entry: {fmt(price, decimals)}\n"
                  f"Stop Loss: {fmt(sl, decimals)}\n"
                  f"Take Profit: {fmt(tp, decimals)}\n"
                  f"Risk:Reward: 1:{rr:.2f}\n"
                  f"Confidence: {conf:.2f}\n"
                  f"{sizing}"
                  f"Risk Check: APPROVED\n"
                  f"- REGIME_FILTER: multiplier applied, size {final}% reflects {regime}\n")
    return instruction, inp, output


def gen_meme_atr_reject():
    sym, price0, decimals, _ = pick_symbol(meme=True)
    price = round(price0 * rng.uniform(0.8, 1.3), decimals)
    atr = rng.uniform(4.1, 7.5)
    instruction = f"Analyze {sym} (meme coin) on the 1H. Generate a RUNECLAW TradeIdea with risk verdict."
    inp = (f"RSI {rng.randint(25, 33)}, MACD positive, volume {rng.uniform(2.2, 3.4):.1f}x — a clean-looking "
           f"long. ATR is {atr:.1f}% of price; the meme-coin ATR guard is 4.0%.")
    output = (f"Trade Scan: {sym}\n"
              f"Direction considered: LONG\n"
              f"Risk Check: REJECTED\n\n"
              f"Failed Checks:\n"
              f"- VOLATILITY: ATR {atr:.1f}% exceeds the 4.0% meme-coin guard\n\n"
              f"Reasoning: Meme-coin volatility at {atr:.1f}% makes any stop placement a coin "
              f"flip; the guard exists because these fills gap. Signals do not override it.\n\n"
              f"Action: NO TRADE. Capital preservation above all.")
    return instruction, inp, output


# The trained 12-voter table from the Modelfile spec — NOT the invented
# indicator sets the model hallucinated in ad-hoc chat (VPVR, CVD,
# "liquidity pool proximity" with made-up weights).
VOTERS = [
    ("RSI-14", 1.5), ("MACD Histogram", 1.0), ("Bollinger %B", 1.0),
    ("Volume Spike", 0.8), ("ADX Trend", 0.7), ("OBV Trend", 0.6),
    ("VWAP", 0.5), ("EMA Ribbon 9/21", 0.5), ("Fibonacci Zone", 0.5),
    ("Keltner Squeeze", 0.7), ("Chart Patterns", 0.7), ("Candlestick", 0.8),
]
TOTAL_WEIGHT = sum(w for _, w in VOTERS)  # 9.3


def gen_full_report():
    """The full sectioned GETCLAW report — the format the operator wants as
    'the full response' — built on the TRAINED voter table with the
    confluence and confidence arithmetic actually computed, never asserted."""
    sym, price0, decimals, _ = pick_symbol(meme=False)
    price = round(price0 * rng.uniform(0.85, 1.15), decimals)
    tf = rng.choice(["1H", "4H"])
    direction = rng.choice(["LONG", "SHORT"])
    sign = 1 if direction == "LONG" else -1

    adx = rng.randint(16, 38)
    if adx > 25:
        regime = "TREND_UP" if direction == "LONG" else "TREND_DOWN"
    elif adx < 20:
        regime = "RANGE"
    else:
        regime = "CHOP"

    votes = []
    for name, weight in VOTERS:
        if name == "ADX Trend":
            # Derived from the ADX value, never rolled: the same number
            # drives the vote, the payload reading, AND the regime line,
            # so the three can never contradict each other.
            vote = sign if adx > 25 else 0
        else:
            # 66% agree / 22% neutral / 12% disagree lands the confidence
            # distribution near the 0.55 gate, so BOTH verdict branches are
            # trained in this format (measured ~45/55 approve/reject).
            roll = rng.random()
            vote = sign if roll < 0.66 else (0 if roll < 0.88 else -sign)
        votes.append((name, weight, vote))
    weighted_sum = sum(w * v for _, w, v in votes)
    confluence = round((weighted_sum / TOTAL_WEIGHT + 1) / 2, 2)
    conf = abs(confluence - 0.5) * 2 * 0.5 + 0.20
    bonus = ""
    if regime in ("TREND_UP", "TREND_DOWN"):
        conf += 0.10
        bonus = f" +0.10 ({regime} aligned)"
    elif regime == "RANGE":
        conf -= 0.05
        bonus = " -0.05 (RANGE)"
    else:
        conf -= 0.08
        bonus = " -0.08 (CHOP)"
    conf = round(max(0.20, min(0.92, conf)), 2)
    approved = conf >= 0.55

    # Payload consistent with the votes: vote is +1 bullish / -1 bearish /
    # 0 neutral in ABSOLUTE terms (same convention the table prints), so every
    # concrete reading below is derived from the vote — the table and the
    # payload can never disagree.
    def reading(name, vote):
        if name == "RSI-14":
            val = rng.randint(26, 36) if vote == 1 else rng.randint(64, 78) if vote == -1 else rng.randint(45, 55)
            return f"RSI-14: {val}"
        if name == "ADX Trend":
            di = "+DI leading" if vote == 1 else "-DI leading" if vote == -1 else "DI lines flat"
            return f"ADX: {adx} ({di})"
        if name == "Bollinger %B":
            b = rng.randint(5, 15) if vote == 1 else rng.randint(85, 95) if vote == -1 else rng.randint(40, 60)
            return f"Bollinger %B: 0.{b:02d}"
        if name == "Volume Spike":
            return f"Volume: {rng.uniform(1.5, 2.8):.1f}x average" if vote != 0 \
                else f"Volume: {rng.uniform(0.8, 1.1):.1f}x average (no spike)"
        if name == "VWAP":
            offset = rng.uniform(0.003, 0.009) * vote
            return f"Price at {1 + offset:.3f}x VWAP"
        lbl = "bullish" if vote == 1 else ("bearish" if vote == -1 else "neutral")
        return f"{name}: {lbl}"
    payload = ". ".join(reading(n, v) for n, _w, v in votes)
    payload = f"Price: {fmt(price, decimals)}. ATR: {rng.uniform(1.4, 2.6):.1f}% of price. {payload}."

    rr = round(rng.uniform(1.3, 3.0), 2)
    sl, tp, rr = trade_numbers(price, decimals, direction, rr)

    rows = "\n".join(
        f"| {i + 1} | {name} | "
        f"{'BULLISH' if v == 1 else 'BEARISH' if v == -1 else 'NEUTRAL'} | "
        f"{w:.1f} | {w * v:+.1f} |"
        for i, (name, w, v) in enumerate(votes))
    verdict_block = (
        f"Risk Check: APPROVED\n- CONFIDENCE: {conf:.2f} >= 0.55\n"
        f"- RISK_REWARD: {rr:.2f} >= 1.2\n"
        f"\nStatus: PENDING — type CONFIRM to execute" if approved else
        f"Risk Check: REJECTED\n\nFailed Checks:\n"
        f"- CONFIDENCE: {conf:.2f} < 0.55 minimum\n\nAction: NO TRADE.")
    trade_block = (
        f"Entry     : {fmt(price, decimals)}\n"
        f"Stop Loss : {fmt(sl, decimals)}\n"
        f"Take Profit: {fmt(tp, decimals)}\n"
        f"Risk:Reward: 1:{rr:.2f}\n" if approved else
        "No trade parameters issued on a rejection.\n")

    instruction = (f"Analyze {sym} on the {tf}. Produce the FULL RUNECLAW report: the 12-voter "
                   f"confluence table, regime and confidence calculation, trade parameters, "
                   f"and risk check verdict.")
    output = (f"RUNECLAW GETCLAW CONFLUENCE ENGINE\n"
              f"{sym} — {tf} scan (data as provided; nothing is invented)\n\n"
              f"SECTION 1 — Confluence Table (12 weighted voters)\n"
              f"| # | Indicator | Signal | Weight | Score |\n"
              f"|---|-----------|--------|--------|-------|\n"
              f"{rows}\n"
              f"Weighted sum: {weighted_sum:+.1f} / {TOTAL_WEIGHT:.1f} -> "
              f"Confluence = ({weighted_sum:+.1f}/{TOTAL_WEIGHT:.1f} + 1)/2 = {confluence:.2f}\n\n"
              f"SECTION 2 — Regime & Confidence\n"
              f"ADX {adx} -> {regime}\n"
              f"Confidence = |{confluence:.2f} - 0.5| x 2 x 0.5 + 0.20{bonus} = {conf:.2f}\n\n"
              f"SECTION 3 — Trade Parameters ({direction})\n"
              f"{trade_block}\n"
              f"SECTION 4 — Verdict\n"
              f"{verdict_block}")
    return instruction, payload, output


def gen_no_data_refusal():
    """Bare 'BTC'-style prompts in ad-hoc chat produced fully fabricated
    scans (invented prices, invented '19/23 checks passed' under a
    fail-closed policy that would mean REJECTED). Absent data must not
    render as a confident scan — the model asks for the payload instead."""
    sym, _, _, _ = pick_symbol()
    base = sym.split("/")[0]
    instruction = rng.choice([
        base, f"{base} scan", f"scan {base}", f"analyze {base}", f"{sym}",
        f"{base}?", f"trade idea {base}", f"should we long {base}",
    ])
    output = (f"Trade Scan: {sym}\n"
              f"Risk Check: REJECTED\n\n"
              f"Failed Checks:\n"
              f"- STALE_DATA: no market data was provided — not a single indicator "
              f"can be computed\n\n"
              f"Reasoning: I do not invent prices, indicator readings, or check "
              f"results. A scan without data would be fiction in a confident "
              f"format, which is worse than no answer. To run the full analysis, "
              f"provide: current price, RSI-14, MACD histogram, Bollinger %B, "
              f"volume vs average, EMA9/EMA21 relation, OBV trend, ATR (absolute "
              f"and % of price), and any relevant ACCOUNT STATE (drawdown, daily "
              f"P&L, open positions, cooldowns).\n\n"
              f"Action: NO TRADE. Awaiting market data.")
    return instruction, "", output


BUILDERS = [
    (gen_state_gate_reject,     0.24),
    (gen_healthy_state_approve, 0.18),
    (gen_rr_reject,             0.10),
    (gen_rr_approve,            0.12),
    (gen_regime_sizing,         0.14),
    (gen_meme_atr_reject,       0.06),
    (gen_no_data_refusal,       0.06),
    (gen_full_report,           0.10),
]


def main():
    parser = argparse.ArgumentParser(description="Generate the v8 targeted training set")
    parser.add_argument("--count", type=int, default=12000)
    parser.add_argument("--seed", type=int, default=8)
    parser.add_argument("--output", default="training_data/v8_targeted.jsonl")
    args = parser.parse_args()

    rng.seed(args.seed)
    counts = {}
    rows = []
    builders = [b for b, _ in BUILDERS]
    weights = [w for _, w in BUILDERS]
    for _ in range(args.count):
        builder = rng.choices(builders, weights=weights)[0]
        instruction, inp, output = builder()
        rows.append({"instruction": instruction, "input": inp, "output": output})
        counts[builder.__name__] = counts.get(builder.__name__, 0) + 1

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    sha = hashlib.sha256(open(args.output, "rb").read()).hexdigest()
    approve = counts.get("gen_healthy_state_approve", 0) + counts.get("gen_rr_approve", 0)
    manifest = {"seed": args.seed, "count": args.count, "per_builder": counts,
                "output": args.output, "sha256": sha}
    manifest_path = os.path.join(os.path.dirname(args.output) or ".", "V8_GENERATION_MANIFEST.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print("=" * 60)
    print("RUNECLAW v8 - Targeted Data Generated")
    print("=" * 60)
    for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>6}  {name}")
    print(f"\n  Pure-approve samples: {approve} (~{approve / args.count:.0%} — the anti-timidity balance;")
    print("  regime_sizing adds more approvals on top)")
    print(f"  Output:   {args.output}")
    print(f"  Manifest: {manifest_path}")
    print(f"  SHA256:   {sha[:16]}...")
    print("\nNext steps:")
    print("  python curate_training_data.py --output training_data\\curated_v8_final.jsonl ^")
    print("      --input training_data\\v8_targeted.jsonl training_data\\curated_v8_all.jsonl")
    print("  python train_runeclaw_v7_8b.py --data training_data\\curated_v8_final.jsonl --run-name v8")


if __name__ == "__main__":
    main()
