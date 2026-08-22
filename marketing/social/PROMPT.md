# Prompting for a usable RUNECLAW clip

Two Grok generations (2026-08-21) were unusable for the same two reasons, and
both are properties of the tool rather than of the prompt wording:

1. **It invents numbers.** Clip 1 rendered `TOP SIGNAL · BTC/USDT · +2.47% ·
   HIGH CONFIDENCE`, drifting to `+2.51%`. Clip 2 rendered `10,000+ ACTIVE
   USERS`, `95% USER SATISFACTION`, `PROVEN RESULTS`, a `78%` gauge and an
   animated `0.4580 / 13% / 0.8% / 25%` counter over fabricated equity curves.
   The user count is the sharp one: the box reported **18 users**.

2. **It garbles text.** Clip 2: `HUMANOID TRADEERS`, `BY INTFEE.I GENCE.`,
   `BUJSTED BY TRAEIEVC.`, `KRESL EMIZFTAREStORM`, `OINOSIHILE`,
   `CCART EOFATECO`. No prompt fixes this; diffusion models do not spell.

So: **ask for motion and mood, never for words or figures.** Add every word in
post, where it can be spelled correctly and where each claim can be checked
against `site/src/facts.ts`.

## Prompt

> Vertical 9:16 cinematic product film, 15 seconds. Dark near-black
> environment (#0a0b10) with a single electric-blue key light (#3fb6ff). A
> matte-white humanoid figure stands in three-quarter profile, still and
> composed, in a quiet control room. Slow push-in, shallow depth of field,
> volumetric haze, subtle blue rim light. Abstract geometric light forms drift
> behind it — hexagonal lattices and thin flowing lines, no charts, no candles,
> no graphs, no dashboards, no gauges.
>
> ABSOLUTELY NO TEXT, no letters, no numbers, no digits, no percentages, no
> logos, no watermarks, no user interface, no captions, no signage of any kind
> anywhere in the frame.
>
> Restrained and clinical, not triumphant. Reference: a Linear or Vercel
> product film, not a crypto advertisement.

Negative prompt, if the tool takes one:

> text, letters, numbers, digits, percentages, charts, candlestick charts,
> graphs, dashboards, UI, gauges, logos, watermarks, captions, subtitles,
> purple, violet, gold, neon green

## Then add the words in post

    ffmpeg -i clip.mp4 -vf "drawtext=fontfile=<font>:text='PAPER FIRST':\
      fontcolor=0x3fb6ff:fontsize=48:x=(w-text_w)/2:y=h*0.72" out.mp4

Only claims the code backs, checked against the code and not against README.
Today that is: paper is the default (`bot/config.py:2142`), live trading is off
until switched on (`:2143`), and decisions are hashed before the market moves.

NOT "human-confirmed". `bot/config.py:2190` sets
`auto_confirm_live_enabled` to `True` by default, so a signal clearing the 0.85
threshold places a real-money order with no human press. README.md claims
human confirmation in three places and is wrong in all three.

Not a risk-check count, not a user count, not a satisfaction rate, not a return.

Two ffmpeg traps if you script this — both cost time on clip 1:

* In `drawbox` expressions `t` is **thickness**, not time, and `n` is
  undefined. Only `enable='between(t,a,b)'` sees the real clock, so time-varying
  geometry has to be expressed as time-sliced static boxes. In `drawtext`, `t`
  *is* time — so a box and its label written the same way track differently.
* `-ss` resets the filter clock. Sampling a frame at `-ss 12` shows the filter
  at t≈0, so a check of a time-varying filter silently tests the wrong moment.
  Filter the whole clip, then sample the output.
