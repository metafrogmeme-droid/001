"""The reads the website's chat answers from its own intercepts and Telegram
does not — and the door each one is given, on both surfaces.

`app/routes/chat.js` answers nothing from its own intercepts before a
bot round-trip. The table below is empty. Idle yield left it: both
doors route "my idle usdc" to the shared idleyield card, the caller's
linked wallet. Telegram's `/idleyield` stays the operator's exchange
scan, a different reading under the same word, and these words do not
reach that command. Nothing here places, confirms, sizes, or stakes.
"stake my usdc" stays the stake door. "what's my drawdown", "am I over
my exposure", "check my risk" and "what's my max exposure" stay the
risk engine. Dollars stay on this private card. /api/idleyield still
serves the read while the bot process is down.
Net worth is not among the empty table: both doors route "my net worth" to
`/networth`, the caller's own book — the connected exchange plus the
on-chain wallet, paper labelled simulated and never added in. The read
is that caller's book, never the operator's. Nothing here places,
confirms, sizes, or closes. "what's my drawdown", "am I over my
exposure", "check my risk" and "what's my max exposure" stay the risk
engine. Dollars stay on this private card. /api/networth still serves
the read while the bot process is down.
The research dossier is not among the empty table: both doors route "research
SOL" to `/research`, the one symbol the sentence names. The card is
public venue data plus the recorded history, for every caller.
Nothing here places, confirms, sizes, or closes a trade. "deep dive
on SOL" stays the chart; "research the docs" and "research report"
stay the model. /api/research/:symbol still serves the dossier while
the bot process is down.
Cross-venue exposure is not in this table: both doors route "my
exposure" to `/exposure`, the caller's perp positions netted against
their on-chain spot. The read is that caller's book, never the
operator's. Nothing here resizes, hedges, or closes a position.
"what's my drawdown" and "check my risk" stay the risk engine; the
words the website's intercept used to claim are this card on both
surfaces. /api/exposure still serves the read while the bot process
is down.
The DeFi positions are not in this table: both doors route "my defi
positions" to `/defi`, the caller's Aave, Lido and Uniswap positions
with their liquidation risk. The read is that caller's linked wallet,
never somebody else's and never the operator's book. Nothing here
repays, withdraws, or manages a position. /api/defi still serves the
read while the bot process is down.
The spot market is not in this table: both doors route "spot market"
to `/spot`, the spot pairs and the spot/perp basis. Venue tickers are
public, for every caller. Nothing here places a spot order.
/api/spot/market and /api/spot/basis still serve the read while the bot
process is down.
The NFT radar is not in this table: both doors route "nft radar"
to `/nft`, the OpenSea floor and volume snapshot. Collection stats are
public, for every caller. /api/nft/radar still serves the radar while
the bot process is down.
The meme radar is not in this table: both doors route "meme radar"
to `/meme_radar`, the on-chain snapshot with its safety read. The feed
is public DEXScreener data, for every caller. The Markets panel and
/api/market/meme still serve the radar while the bot process is down.
The venue router is not in this table: both doors route "best venue
for BTC" to `/venue_router`, the funding-cost table. The asset the
sentence names narrows the card; an unnamed asset is the top five.
The Markets panel and /api/market/venue-router still serve the read
while the bot process is down.
The airdrop and testnet radar is not in this table: both doors route it
to `/airdrops`, the curated guided-only catalogue. Wallet-readiness hints
are the caller's own when the website can map them, and the public radar
otherwise — never a guessed wallet.
The tokenized-asset radar is not in this table: both doors route it to
`/rwa`, the venue's live tickers.
The what-if replay is not in this table: both doors route it to
`/replay`, the operator agent's recorded trades at the caller's stake.
The wallet mirror is not in this table: both doors route it to
`/wallet`, the caller's own linked wallet. The weekly letter is not
in this table either: both doors route it to `/letter`. Price alerts
are not among them either: both doors route them to
`/price_alert`, a WRITE the website's alert engine holds and delivers on
Telegram too since the bot polls its trips; the command is not called
`/alerts` because that name is the anomaly-alert scope. Idle yield is not
in this table either: both doors route it to the shared idleyield card.
Typed on Telegram before any of that, "replay every
signal with $1k" ran a SYNTHETIC BACKTEST — the backtest rule carried a bare
`replay` — and the other eight reached the social gate or a model told
nothing about the website, which then answered from nothing; "idle yield"
and "my idle usdc" were greeted.

A read the product has on one surface and not the other gets a DOOR, never a
narrator: the notice names the surface that answers it, the phrasing that
surface accepts, the command that shares the word when one does, and ends by
saying nothing was read — because a routed request that is answered at all
must say whether anything was.

`web_reads.json` is the one table. The Node side reads it too
(`app/test/web_reads_examples_reach_the_intercepts.test.js` drives every
`example` through the intercept's own pattern), so the sentence the notice
tells a caller to type is one the website really answers — a phrasing that
drifted out of an intercept's regex would fail there, not in a user's chat.
"""
from __future__ import annotations

import json
import pathlib
from typing import NamedTuple, Optional


class WebRead(NamedTuple):
    intent: str
    row: str                 # the intercept row in app/routes/chat.js
    lib: str                 # app/lib/<lib>.js, whose CHAT_RE is the claim
    label: str               # what it is, in a person's words
    example: str             # a phrasing the intercept accepts, verbatim
    collides_with: Optional[str]   # a Telegram command sharing the word


_TABLE = pathlib.Path(__file__).with_name("web_reads.json")


def _load() -> dict[str, WebRead]:
    raw = json.loads(_TABLE.read_text(encoding="utf-8"))
    return {name: WebRead(name, r["row"], r["lib"], r["label"], r["example"],
                          r.get("collides_with"))
            for name, r in raw.items()}


WEB_READS: dict[str, WebRead] = _load()


def collision_blurb(command: str) -> str:
    """What the Telegram command sharing the word DOES — read off the command
    catalogue, never written here, so the sentence cannot say a command does
    something it does not. Raises for a name the catalogue lacks: a notice
    naming an unknown command would be the /vault hint shape."""
    from bot.skills.command_catalog import all_entries
    _title, _aud, desc = all_entries()[command]
    return desc


def web_read_notice(intent: str, surface: str = "telegram") -> str:
    """The door, on both surfaces, for a read only the website answers.

    Telegram: the read exists, on the web app's chat, in these words; nothing
    was read here; and when a Telegram command shares the word, what THAT one
    does — a caller typing "my alerts" after reading the web's card would
    otherwise take the anomaly-scope card as the price alerts they set.

    Web: the Python path only sees this ask when the Node intercept's own
    pattern missed the phrasing, so the honest answer is the phrasing it
    accepts. Both end by saying nothing was read; both name no slash command
    except the colliding one, which the catalogue vouches for.
    """
    r = WEB_READS[intent]
    if surface == "web":
        return (f"This chat answers {r.label} itself — ask in these words: "
                f"“<i>{r.example}</i>”. Nothing was read or set for this "
                "message.")
    head = r.label[0].upper() + r.label[1:]
    out = (f"\U0001f310 <b>{head}</b> is handled by the RUNECLAW web app's "
           "chat, from its own records, and not by this one — ask it there "
           f"in the same words: “<i>{r.example}</i>”. Nothing was read or "
           "set here.")
    if r.collides_with:
        out += (f" This chat's <code>/{r.collides_with}</code> is "
                f"{collision_blurb(r.collides_with)} — a different thing "
                "under the same word.")
    return out
