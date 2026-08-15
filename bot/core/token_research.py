"""One address in, one dossier out — with the gaps named.

The four scorers were pure and correct and reachable by nothing: `token_dossier`
and `presale_claims` were imported by zero non-test modules. This is the thin
orchestration that makes them answerable, and it is deliberately thin — every
judgement stays in the module that owns it.

WHAT IT REFUSES TO DO

It does not fill a gap to make the report look complete. No deployer source
exists yet, so `deployer_report` is None, and `token_dossier` records that
section as **not read** rather than omitting it — a two-section report with one
section quietly missing reads as a complete one-section report, which is the
failure that whole module is built around.

So today's honest output for a healthy token is `UNPROVEN` with `deployer` in
`blind_spots`, not `WATCH`. That is the correct answer and it will stay the
correct answer until somebody wires a deployer source. A dossier that said
`WATCH` here would be claiming the deployer checked out.
"""
from __future__ import annotations

from typing import Optional, Sequence

from bot.core import token_dossier
from bot.core.token_safety import assess_token, coverage
from bot.core.token_sources import DexScreenerSource, gather


def default_sources() -> list:
    """The sources that need no API key.

    Kept as a function rather than a module constant so a caller can add paid
    ones without this list becoming a hidden global. A source absent from here
    is not "off" — `gather` reports every source it was handed, including the
    ones it could not use, so adding a keyless-but-unconfigured source later
    still produces an honest `unavailable` row rather than silence.
    """
    return [DexScreenerSource()]


async def investigate(address: str, chain: str = "eth",
                      sources: Optional[Sequence] = None,
                      timeout: float = 8.0) -> dict:
    """Research one token. Returns::

        {address, chain, sources, contract, dossier}

    Nothing here decides anything: `gather` says who answered, `assess_token`
    scores the mechanics, `token_dossier` composes. The only judgement this
    function makes is which sources to ask.
    """
    g = await gather(sources if sources is not None else default_sources(),
                     chain, address, timeout=timeout)

    contract = assess_token(g["features"])
    contract["coverage"] = coverage(contract["checks"])

    # A section is only attributed to a source when a source actually supplied
    # something for it. `provenance` is empty when nothing was read, and an
    # attribution on an unread section would be a citation for a claim nobody
    # made.
    section_sources = {}
    if g["provenance"]:
        section_sources["contract"] = ", ".join(sorted(set(g["provenance"].values())))

    dossier = token_dossier.compose(
        token_report=contract,
        # No deployer source exists yet. Passing None makes the dossier record
        # the section as unread; inventing an empty report would make it look
        # examined and clean.
        deployer_report=None,
        sources=section_sources,
    )

    return {"address": address, "chain": chain,
            "sources": g, "contract": contract, "dossier": dossier,
            # Fields a source supplied that no check consumes. Found the hard
            # way: a fixture supplying `honeypot` where the scorer reads
            # `honeypot_cannot_sell` produced a source that answered, was
            # credited in the provenance map, and contributed nothing — a
            # misconfigured integration that looks exactly like a working one,
            # right down to "1 of 1 answered". Naming them costs a set
            # difference and turns a silent misconfiguration into a visible
            # one.
            "unused_fields": sorted(
                set(g["features"]) - {c["name"] for c in contract["checks"]})}


def human_readable(result: Optional[dict]) -> str:
    """Plain-text render (no markup): the verdict, then where it came from."""
    if not result or not isinstance(result, dict):
        return "No research result."
    from bot.core.token_sources import human_readable as sources_text
    lines = [
        token_dossier.human_readable(result.get("dossier")),
        "",
        sources_text(result.get("sources")),
    ]
    unused = result.get("unused_fields") or []
    if unused:
        lines.append(f"   ! supplied but unused: {', '.join(unused)} — a source "
                     "is sending fields no check reads")
    return "\n".join(lines)
