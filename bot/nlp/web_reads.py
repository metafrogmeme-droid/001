"""The reads the website's chat answers from its own intercepts and Telegram
does not — and the door each one is given, on both surfaces.

`app/routes/chat.js` answers fifteen shapes of question before any bot
round-trip. Six have a Telegram command that renders the same reading and
are routed to it (`networth`, `rwa`, `research`, and — since the website's
own cards became commands — `nft`, `spot`, `airdrops`); three collide by
NAME with a Telegram command that does something else (`/alerts` is the
anomaly-alert scope, `/venues` picks which connected venues trade,
`/memeplan` is a buy preflight); and three have nothing on Telegram at all
(the what-if replay, the weekly letter, the DeFi positions). Typed on
Telegram, "replay every signal with $1k" ran a SYNTHETIC BACKTEST — the
backtest rule carried a bare `replay` — and the others reached the social
gate or a model told nothing about the website, which then answered from
nothing.

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
