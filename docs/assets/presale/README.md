# Presale artwork

Three PNGs for Smithii's pages. Smithii is the venue of record (`token/presale/smithii.config.json`).

| File | Goes in | How it is given |
|---|---|---|
| `rclaw_tokenomics_1500x750.png` | the sale form, step 3 of 4, **Tokenomics Image (1500x750)** | a URL, not a file |
| `rclaw_roadmap_1500x750.png` | the sale form, the roadmap image | a URL, not a file |
| `rclaw_tokenomics_1000x1000.png` | the Token Vesting tool, Advanced Options, **Tokenomics Image (optional)**, `.png · .jpg · 1000×1000 px` | an upload, once per lock (`npm run presale:locks-plan` names it) |

The two sale-form images are served from this repository once they are on `main`:

    https://raw.githubusercontent.com/metafrogmeme-droid/001/main/docs/assets/presale/rclaw_tokenomics_1500x750.png
    https://raw.githubusercontent.com/metafrogmeme-droid/001/main/docs/assets/presale/rclaw_roadmap_1500x750.png

(the repository is public, and the same URL pattern serves `docs/gitbook/*.png` as `image/png` today).
**Prefer the commit-pinned form**, the same URL with the merge commit's hash where `main` is: a `main` URL
shows whatever is on `main` later, so a redrawn image would silently change what the sale page shows after
people have bought, while a hash never changes. Smithii allows a sale to be edited only until its first
phase starts (`smithii.config.json`, `disclosures.proceedsGoToTheSigningWallet`), so the URL is chosen once,
before the sale opens.

## What the tokenomics card shows, and where each figure comes from

The 1500x750 and the 1000x1000 are the same content in two shapes and carry the same record.

| On the image | Read from |
|---|---|
| 1,000,000,000 fixed supply, mint and freeze revoked | `token/config/rclaw.mainnet.json` |
| The eight allocation rows (percent and tokens) | `docs/TOKEN_ROADMAP.md` section 4, the ratified table |
| The note under Community, Team and Advisors ("5 unlocks: at TGE, then every 9 mo", ...) | `token/presale/locks.plan.json`, worded by `locks_lib.mjs` (`shortNote`) |
| Presale 15% / 150M, the rate, 5,000 SOL hard cap, 0.25-25 SOL per wallet, the window 15 Oct → 29 Oct 2026 | `token/presale/smithii.config.json`. The rate is the **stored** price: 30,000.3 per SOL (33,333 whole lamports per token), not the 30,000 the form's Sale Rate line shows, because Smithii stores whole lamports (`sale._priceNote`). |
| "Pool follows the raise: ≈20.0-100.0M" and "66.67% of the SOL raised" | `smithii.config.json`, `liquidity`. It is the operator's promise; the program enforces neither. |
| The 1,000 SOL soft cap "is a target, not enforced" | the same file: the program stores a soft cap and never reads it |
| The loop: platform fees and monthly AI-service payments feed a burn | the operator's stated intent, drawn as **PLANNED**. Nothing in the code collects fees or monthly payments yet, and no split between burn and anything else is set anywhere, so the image shows none. |

Percentages are shown rounded (2.0001% as 2%, 12.9999% as 13%); the exact token counts are in the
roadmap table. The allocation is labelled PLANNED because the roadmap calls it a proposed baseline and the
locks it describes have not been created: one wallet held all 1,000,000,000 on 2026-10-10 (Jupiter's token
index, `holderCount` 1, read at 10:32 UTC).

## What the roadmap card shows

| On the image | Read from |
|---|---|
| The six phases and their items | `docs/TOKEN_ROADMAP.md` section 8 |
| The two ticks (1B fixed supply minted; mint and freeze authority revoked) | `token/config/rclaw.mainnet.json`; the test refuses a tick the token record does not support |
| "Public sale 15-29 Oct 2026, 5,000 SOL hard cap" | `smithii.config.json`, `schedule` and `sale` |
| "Claim 100% when the sale ends", no refunds, no buyer vesting | `smithii.config.json` and roadmap section 10: the program has no vesting and no refund |

Only the sale dates are set; the card says so. The phase status chips (in progress, next, planned) and
which items are still open circles are the generator's, not read from anywhere: nothing here can tell that
a gate was cleared, which is why the card ticks only what the token record shows.

## They are second copies of those numbers, so they are guarded

The generator wrote the exact figures it drew into each PNG (a `tEXt` chunk named `runeclaw-tokenomics` or
`runeclaw-roadmap`). `token/presale/sale_page_images.test.mjs` reads them back and compares them with the
roadmap table, the sale config, the locks plan and the token record, so changing any of those (the 10% / 5%
question in roadmap section 13 is open; the sale's dates are a config value) without redrawing the images
fails CI. It checks what an image says it was drawn from, not its pixels.

Two consequences:

- **Do not re-save these files through an image editor or host and commit the result.** That strips the
  chunk and the test fails by design. Upload a copy to a host if one is needed; keep these as the source.
- **To change a figure, redraw the image.** The generator is not in the repository; the images were drawn on
  2026-10-10 from the figures above, with the wordmark in Cinzel (the logo face) and the rest in Rajdhani
  and Inter. Ask for a redraw rather than editing a PNG.

The sale page links the two sale-form images by URL. If a figure changes after Create, redraw **and**
re-point the page at the new image, which Smithii allows only until the first phase starts.
