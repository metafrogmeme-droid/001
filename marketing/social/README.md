# Social assets

Off-site distribution only. **Nothing in this directory is served** —
`api_bridge.py` mounts `website/` at `/` and nothing else, so these files reach
people only when somebody uploads them somewhere.

## runeclaw-promo-15s-2x3.mp4 · runeclaw-promo-15s-9x16.mp4

15s promo, derived from an AI-generated source clip (Grok, 2026-08-21).

### What was changed, and why

The source clip rendered a chart panel reading:

    TOP SIGNAL
    BTC/USDT
    +2.47%          ← drifts to +2.51% by the final frame
    HIGH CONFIDENCE

That is a fabricated trading signal with a confidence label and a return
figure, animated so the number changes on screen. It is the same defect as the
`$72,669` BTC price frozen into `submission.html` — a figure that reads as a
measurement, which nobody measured — except attached to a *performance* claim
on a financial product, which is the version that carries real consequences.

CLAUDE.md's rule is "unreadable is never zero, and absent is never a
measurement". Inventing a measurement outright is the same rule broken harder.

So the panel is **masked** in both outputs and replaced with the product's
actual promise:

    PAPER FIRST
    RISK-GATED
    HUMAN-CONFIRMED

Three claims the code backs, in the voice `docs/ROADMAP.md` and the platform
already use.

### How the mask is built

The clip is a Ken Burns zoom over a static poster, so the panel moves and
scales continuously — a fixed box cannot cover it, and there is no segment
where it is absent. The mask is therefore **eight time-sliced boxes** whose
geometry comes from a quadratic fit through the panel position measured at
t=0, t=7 and t=14, sampled at 13 points per slice and padded 48px.

Two ffmpeg traps are baked into that choice and are worth knowing:

* **`drawbox` expressions have no time variable.** `t` inside a `drawbox`
  expression is the *thickness*, not the timestamp, and `n` is undefined
  entirely. A tracked box written the obvious way silently evaluates its
  position from the border width — which is why the first attempt drew the
  fill and the border in two different wrong places while the `drawtext`
  layer, where `t` *is* time, tracked correctly.
* **`-ss` resets the filter clock.** Sampling a frame at `-ss 12` to check a
  time-varying filter shows you the filter at t≈0, so the check passes or
  fails for reasons unrelated to the thing being checked. Verify by filtering
  the whole clip and sampling the *output*.

`enable='between(t,a,b)'` uses the real timeline and is unaffected by both,
which is why the mask is expressed as slices rather than as motion.

### Regenerating

The source clip is not committed (9.7MB, and it is an input rather than a
deliverable). The filter is reproducible from the measurements above; see the
session that produced it, or re-measure with:

    ffmpeg -i SOURCE -vf "drawgrid=w=96:h=96:t=1:c=yellow@0.5" -ss N -frames:v 1 grid.jpg

### Known limits

* The clip's remaining copy — "SMARTER TRADING. STRONGER FUTURE.", "THE FUTURE
  OF TRADING IS HERE." — is generic hype, not the product's voice, and the
  humanoid-robot art is the AI-stock aesthetic the site rebuild deliberately
  drops. Masking fixed the false claim; it did not make this on-brand.
* "PERFORMANCE OPTIMIZED — Continuous learning for better results over time"
  is still legible in the first ~5 seconds. It is a capability claim nothing in
  the repo backs. It is small and brief, and it was left rather than adding a
  second mask over the feature strip; if this gets real distribution, cut it.
* The palette carries purple/violet accents. The brand accent is
  `--gold: #3fb6ff` (electric rune-blue, historical token name) from
  `app/public/styles.css`.
