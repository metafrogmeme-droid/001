# Presale artwork

## `rclaw_tokenomics_1500x750.png`

The image for the **Tokenomics Image (1500x750)** field on Smithii's Create form (step 3 of 4), which
takes a URL, not a file. Once this file is on `main` its address is

    https://raw.githubusercontent.com/metafrogmeme-droid/001/main/docs/assets/presale/rclaw_tokenomics_1500x750.png

(the repository is public, and the same URL pattern serves `docs/gitbook/*.png` as `image/png` today).

What it shows, and where each figure comes from:

| On the image | Read from |
|---|---|
| 1,000,000,000 fixed supply, mint and freeze revoked | `token/config/rclaw.mainnet.json` |
| The eight allocation bars (percent per bucket) | `docs/TOKEN_ROADMAP.md` section 4, the ratified table |
| Presale 15% / 150M, 30,000 per SOL, 5,000 SOL hard cap, 72 hours | the same table and `token/presale/smithii.config.json` |
| The loop: platform fees and monthly AI-service payments feed a burn | the operator's stated intent, drawn as **PLANNED**. Nothing in the code collects fees or monthly payments yet, and no split between burn and anything else is set anywhere, so the image shows none. |

Percentages are shown rounded (2.0001% as 2%, 12.9999% as 13%); the exact token counts are in the
roadmap table. The allocation is labelled PLANNED because the roadmap calls it a proposed baseline and
the locks it describes (team, advisors, community streams, treasury and reserve multisig) have not been
created: one wallet held all 1,000,000,000 on 2026-10-08 (Jupiter's token index, `holderCount` 1).

### It is a second copy of those numbers, so it is guarded

The generator wrote the exact figures it drew into the PNG (a `tEXt` chunk named
`runeclaw-tokenomics`). `token/presale/tokenomics_image.test.mjs` reads them back and compares them with
the roadmap table and the sale config, so changing the table (the 10% / 5% question in roadmap section 13
is open) or the sale terms without redrawing the image fails CI. The test checks what the image says it
was drawn from, not its pixels.

Two consequences:

- **Do not re-save this file through an image editor or host and commit the result.** That strips the
  chunk and the test fails by design. Upload a copy to a host for the sale page; keep this one as the source.
- **To change a figure, redraw the image.** The generator is not in the repository; the image was drawn on
  2026-10-09 from the figures above, with the wordmark in Cinzel (the logo face) and the rest in Rajdhani
  and Inter. Ask for a redraw rather than editing the PNG.

The sale page links this image by URL. If the table changes, redraw it **and** re-point the sale page at
the new image; whether Smithii lets that page be edited after Create has not been checked here.
