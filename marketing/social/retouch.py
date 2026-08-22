#!/usr/bin/env python3
"""Track and replace the unbacked claims in the Grok promo clip.

Four regions carry claims the code does not support:

    AI EDGE +2.47%            an invented performance figure
    SUPERIOR RESULTS.         a performance claim
    PROVEN PERFORMANCE        "Backtested strategies. Transparent results."
    Your capital is protected. false of leveraged trading, flatly

The poster zooms continuously, so a static box does not hold and a fitted
curve was wrong twice on the previous clip. This matches each region per
frame instead, over a scale sweep, so the mask follows whatever the frame
actually does rather than whatever a model of it predicted.

Replacement copy is drawn from README.md:62 verbatim - 23 pre-trade risk
checks, simulation-first by default - not invented to fill the space.
"""
import subprocess
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

S = "/tmp/claude-0/-home-user-001/60abea1a-6dd5-590d-9a3d-bb659f02c5c9/scratchpad"
SRC = "/root/.claude/uploads/60abea1a-6dd5-590d-9a3d-bb659f02c5c9/d2aa6753-grok_video_20260821211520.mp4"
OUT = f"{S}/clip3_frames"
FONT = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
FONT_R = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"

#: The poster phase only. After ~6.9s the layout cuts to robot close-ups whose
#: background panels carry their own invented tables; those are not trackable
#: and not worth keeping, so the clip ends here.
END_S = 6.4

# name -> (box at the t=2 reference frame, replacement lines)
# Replacement of None means COVER, add nothing: an empty corner is honest and
# an invented badge would just be the same defect in our own words.
REGIONS = {
    "edge":     ((628, 452, 768, 560), None),
    "superior": ((470, 128, 648, 162), None),
    # "23 pre-trade checks" was here and came straight back out. The repo
    # DELIBERATELY removed that number: tests/test_no_hardcoded_risk_check_count.py
    # bans it on seven surfaces, because 23 was hand-maintained, drifted, and
    # understated a gate that emits 36 distinct labels. Replacing one invented
    # figure with a retired one is not a fix.
    "proven":   ((556, 876, 744, 956),
                 [("PAPER FIRST", 17, (0x3f, 0xb6, 0xff), FONT),
                  ("Live trading is off", 13, (0xed, 0xee, 0xf2), FONT_R),
                  ("until you enable it.", 13, (0xed, 0xee, 0xf2), FONT_R)]),
    "capital":  ((384, 930, 552, 958),
                 [("Simulation-first by default.", 13, (0xed, 0xee, 0xf2), FONT_R)]),
}

BG = (0x0a, 0x0b, 0x10)


def main() -> int:
    ref = cv2.imread(f"{S}/ref.png", cv2.IMREAD_GRAYSCALE)
    if ref is None:
        print("no reference frame", file=sys.stderr)
        return 2

    tmpl = {}
    for name, (box, _) in REGIONS.items():
        x0, y0, x1, y1 = box
        tmpl[name] = ref[y0:y1, x0:x1]

    cap = cv2.VideoCapture(SRC)
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    n_end = int(END_S * fps)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    vw = cv2.VideoWriter(f"{S}/clip3_silent.mp4", fourcc, fps, (w, h))

    scales = np.arange(0.80, 1.45, 0.03)
    misses = {k: 0 for k in REGIONS}
    n = 0
    while n < n_end:
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        d = ImageDraw.Draw(pil)

        for name, (box, lines) in REGIONS.items():
            t = tmpl[name]
            best = (-1.0, None)
            for s in scales:
                th, tw = int(t.shape[0] * s), int(t.shape[1] * s)
                if th < 8 or tw < 8 or th >= h or tw >= w:
                    continue
                ts = cv2.resize(t, (tw, th), interpolation=cv2.INTER_AREA)
                r = cv2.matchTemplate(gray, ts, cv2.TM_CCOEFF_NORMED)
                _, mx, _, ml = cv2.minMaxLoc(r)
                if mx > best[0]:
                    best = (mx, (ml[0], ml[1], tw, th))
            score, loc = best
            # A weak match means the region left the frame or the layout cut.
            # Skipping is right: painting a box at a guessed position would
            # cover something else and look like a mistake, which it would be.
            if score < 0.55 or loc is None:
                misses[name] += 1
                continue
            x, y, bw, bh = loc
            pad = 3
            box = (max(0, x - pad), max(0, y - pad),
                   min(w, x + bw + pad), min(h, y + bh + pad))
            if lines:
                d.rectangle(list(box), fill=BG)
            else:
                # Nothing replaces this one, so blur it back into the plate
                # instead of painting a black rectangle. A redaction advertises
                # what was removed; a soft patch just reads as depth of field.
                from PIL import ImageFilter
                patch = pil.crop(box).filter(ImageFilter.GaussianBlur(14))
                pil.paste(patch, box[:2])
                d = ImageDraw.Draw(pil)
            if lines:
                cy = y + 6
                for text, size, colour, path in lines:
                    fs = max(9, int(size * (bh / t.shape[0])))
                    f = ImageFont.truetype(path, fs)
                    tw_, th_ = d.textbbox((0, 0), text, font=f)[2:]
                    d.text((x + bw / 2 - tw_ / 2, cy), text, font=f, fill=colour)
                    cy += th_ + max(2, fs // 4)

        vw.write(cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR))
        n += 1

    cap.release()
    vw.release()
    print(f"frames written: {n}")
    for k, v in misses.items():
        print(f"  {k}: {v} frame(s) unmatched")

    # Re-encode to h264 and carry the original audio for the trimmed range.
    subprocess.run([
        "ffmpeg", "-loglevel", "error", "-y",
        "-i", f"{S}/clip3_silent.mp4",
        "-ss", "0", "-t", str(END_S), "-i", SRC,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-crf", "30", "-preset", "medium",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart", "-shortest",
        f"{S}/clip3_clean.mp4",
    ], check=True)
    print("wrote clip3_clean.mp4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
