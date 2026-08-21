# Social assets

Off-site distribution only. **Nothing in this directory is served** —
`api_bridge.py` mounts `website/` at `/` and nothing else, so these files reach
people only when somebody uploads them somewhere.

| file | ratio | length | size |
|---|---|---|---|
| `runeclaw-promo-2x3.mp4` | 768×1168 | 6.4s | 0.60 MB |
| `runeclaw-promo-9x16.mp4` | 1080×1920 | 6.4s | 0.77 MB |

Derived from an AI-generated source clip (Grok, 2026-08-21, 11.45 MB / 15.0s).
`retouch.py` regenerates both from that source.

## What was changed, and why

Three clips were generated. Every one of them invented figures, because the
prompt asked for a trading product and image models draw trading UI, and
trading UI is made of numbers. What each one claimed:

| clip | invented | outcome |
|---|---|---|
| 1 | `TOP SIGNAL · BTC/USDT · +2.47%` drifting to `+2.51%`, `HIGH CONFIDENCE` | masked, superseded |
| 2 | `10,000+ ACTIVE USERS` (the box reports **18**), `95% USER SATISFACTION`, `PROVEN RESULTS`, a `78%` gauge | unusable — see below |
| 3 | `AI EDGE +2.47%`, `SUPERIOR RESULTS.`, `PROVEN PERFORMANCE`, `Your capital is protected.` | **shipped, retouched** |

Clip 2 was rejected outright. Its false stats sat in a persistent band across
most of the runtime rather than in one panel, so masking would have blacked out
a third of the frame permanently — and it would not have fixed the other half
of the problem, which is that the art reads `HUMANOID TRADEERS`,
`BY INTFEE.I GENCE.`, `BUJSTED BY TRAEIEVC.` and `KRESL EMIZFTAREStORM`.
Diffusion models do not spell. See `PROMPT.md`.

### The four regions replaced in clip 3

    AI EDGE +2.47%              → blurred out (an invented performance figure)
    SUPERIOR RESULTS.           → removed (tagline now ends "HUMAN INTELLIGENCE.")
    PROVEN PERFORMANCE          → PAPER FIRST / Live trading is off / until you enable it.
      "Backtested strategies.
       Transparent results."
    Your capital is protected.  → Simulation-first by default.

Replacement copy is verified against `bot/config.py:2142-2143`:
`simulation_mode` defaults to `True` and `live_trading_enabled` defaults to
`False`. Nothing here was written to fill the space.

**The first cut said "23 pre-trade checks" and that came straight back out.**
README.md says 23, but the repo deliberately RETIRED that number:
`tests/test_no_hardcoded_risk_check_count.py` bans it on seven surfaces because
it was hand-maintained, drifted, and understated a gate that emits 36 distinct
labels. Replacing an invented figure with a retired one is not a fix — it is
the same defect wearing the product's own documentation as cover.

"Your capital is protected" is the one that mattered most. It is not a
sales exaggeration on a leveraged-futures product; it is false, and the
footer of every page on the new site says the opposite in as many words.

The clip is trimmed to 6.4s — the poster phase. After that the source cuts to
robot close-ups whose background panels carry their own invented tables
(`6.30 −5.250`, `+28.70 −3.235`, …), which are neither trackable nor worth
keeping.

## How the retouch works

The poster zooms continuously, so a static box does not hold. Two approaches
were tried before the one that works:

1. **Fitted curve** (linear, then quadratic through three measured points).
   Wrong twice. It tracked well enough to look right in thumbnails and left the
   claim legible at full resolution.
2. **Per-frame template matching** (`retouch.py`). Each region is matched in
   every frame over a scale sweep, so the mask follows what the frame actually
   does rather than what a model of it predicted. 153/153 frames matched on all
   four regions.

A region with no replacement text is **blurred**, not filled: a black rectangle
over a lit plate reads as a redaction, which draws the eye to precisely the
thing being removed.

### Two ffmpeg traps, both of which cost real time

* **`drawbox` expressions have no clock.** Inside one, `t` is the *thickness*
  and `n` is undefined. A tracked box written the obvious way silently
  evaluates its position from the border width — which is why one attempt drew
  the fill and the border at two different wrong places while the `drawtext`
  layer, where `t` *is* time, tracked correctly. Only
  `enable='between(t,a,b)'` sees the real timeline.
* **`-ss` resets the filter clock.** Sampling a frame at `-ss 12` to check a
  time-varying filter shows the filter at t≈0, so the check passes or fails for
  reasons unrelated to what is being checked. Filter the whole clip, sample the
  output.

`retouch.py` uses neither: it composites in OpenCV/PIL per frame, where the
frame index is just a loop variable.

## Known limits

* The remaining copy — "SMARTER TRADING. AUTONOMOUS ADVANTAGE.", "DISCOVER THE
  FUTURE OF TRADING" — is generic, and the humanoid-robot art is the AI-stock
  aesthetic the site rebuild deliberately drops. Retouching removed the false
  claims; it did not make this on-brand.
* The palette carries purple/magenta. The brand accent is `--gold: #3fb6ff`
  (electric rune-blue; the token name is historical) from
  `app/public/styles.css`.
* Small background text in the source is garbled in places. It is illegible at
  playback size rather than wrong, but it is there.
