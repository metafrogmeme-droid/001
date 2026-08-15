"""One address in, one dossier out — with the gaps named.

The four scorers were pure and correct and reachable by nothing: `token_dossier`
and `presale_claims` were imported by zero non-test modules. This is the thin
orchestration that makes them answerable, and it is deliberately thin — every
judgement stays in the module that owns it.

WHAT IT REFUSES TO DO

It does not fill a gap to make the report look complete. A section nothing was
read for is recorded as **not read** rather than omitted — a two-section report
with one section quietly missing reads as a complete one-section report, which
is the failure `token_dossier` is built around.

THE DEPLOYER SECTION

It used to say, here, that no deployer source existed and `deployer_report` was
therefore always None. `deployer_sources.EtherscanDeployerSource` now fills it,
and the ceiling moved less than it looks:

* with no `ETHERSCAN_API_KEY`, the source reports `unavailable` — "we never
  asked" — nothing is read, and the section stays **not read**. Identical
  output to before, now with a named reason;
* with a key, five of the eight facts get real answers. The three that stay
  absent are how the deployer's PREVIOUS contracts ended, and
  `_outcomes_resolved` treats an unknown rug count as fatal, so the verdict
  still cannot reach `clean`.

So a healthy token reads `UNPROVEN` either way — but for opposite reasons, and
the difference is the whole point. Before: unproven because nobody looked.
After: unproven because somebody looked, found a named deployer with a wallet
age and a deployment count, and could not determine how their last ones ended.
The second is a research result. The first was a placeholder.
"""
from __future__ import annotations

from typing import Optional, Sequence

from bot.core import token_dossier
from bot.core.deployer_history import assess_deployer
from bot.core.deployer_sources import default_deployer_sources
from bot.core.token_safety import assess_token, coverage
from bot.core.token_sources import DexScreenerSource, gather

#: Deployer fields that are real output but are not checks — the subject's own
#: address, and the flag saying the history was too long to count. Listed so the
#: `unused_fields` detector does not report them as a misconfigured source; that
#: warning exists to catch a source answering in the wrong dialect, and a false
#: one trains the reader to ignore it.
_DEPLOYER_INFO_FIELDS = frozenset({"deployer_address", "deployments_truncated"})

#: Consumed by `resolve_outcomes` rather than by a named check, so they are read
#: even though no entry in `checks` carries their name.
_DEPLOYER_OUTCOME_FIELDS = frozenset({"prior_deployments", "prior_alive"})


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
                      timeout: float = 8.0,
                      deployer_sources: Optional[Sequence] = None) -> dict:
    """Research one token. Returns::

        {address, chain, sources, deployer_sources,
         contract, deployer, dossier, unused_fields}

    Nothing here decides anything: `gather` says who answered, `assess_token`
    and `assess_deployer` score their own halves, `token_dossier` composes. The
    only judgement this function makes is which sources to ask.
    """
    g = await gather(sources if sources is not None else default_sources(),
                     chain, address, timeout=timeout)

    contract = assess_token(g["features"])
    contract["coverage"] = coverage(contract["checks"])

    dg = await gather(
        deployer_sources if deployer_sources is not None
        else default_deployer_sources(), chain, address, timeout=timeout)

    # Scored only when a source actually supplied something. `assess_deployer({})`
    # would return a well-formed report of all-unknowns — "examined, learned
    # nothing" — where the truth is that nothing was examined. Passing None
    # makes the dossier record the section as unread, which is a different
    # sentence and the correct one.
    deployer = assess_deployer(dg["features"]) if dg["features"] else None

    # A section is only attributed to a source when a source actually supplied
    # something for it. `provenance` is empty when nothing was read, and an
    # attribution on an unread section would be a citation for a claim nobody
    # made.
    section_sources = {}
    if g["provenance"]:
        section_sources["contract"] = ", ".join(sorted(set(g["provenance"].values())))
    if dg["provenance"]:
        section_sources["deployer"] = ", ".join(sorted(set(dg["provenance"].values())))

    dossier = token_dossier.compose(
        token_report=contract,
        deployer_report=deployer,
        sources=section_sources,
    )

    return {"address": address, "chain": chain,
            "sources": g, "deployer_sources": dg,
            "contract": contract, "deployer": deployer, "dossier": dossier,
            # Fields a source supplied that no check consumes. Found the hard
            # way: a fixture supplying `honeypot` where the scorer reads
            # `honeypot_cannot_sell` produced a source that answered, was
            # credited in the provenance map, and contributed nothing — a
            # misconfigured integration that looks exactly like a working one,
            # right down to "1 of 1 answered". Naming them costs a set
            # difference and turns a silent misconfiguration into a visible
            # one.
            # …and the same detector over the deployer half, which needs two
            # extra exemptions: the outcome fields feed `resolve_outcomes`
            # rather than a named check, and the info fields are output that no
            # check is meant to read.
            "unused_fields": sorted(
                (set(g["features"]) - {c["name"] for c in contract["checks"]})
                | (set(dg["features"])
                   - {c["name"] for c in (deployer or {}).get("checks", [])}
                   - _DEPLOYER_OUTCOME_FIELDS - _DEPLOYER_INFO_FIELDS))}


def human_readable(result: Optional[dict]) -> str:
    """Plain-text render (no markup): the verdict, then where it came from."""
    if not result or not isinstance(result, dict):
        return "No research result."
    from bot.core.token_sources import human_readable as sources_text
    dep = result.get("deployer") or {}
    who = (result.get("deployer_sources") or {}).get("features", {}).get(
        "deployer_address")
    lines = [
        token_dossier.human_readable(result.get("dossier")),
    ]
    # Name the deployer when one was actually identified. Everything in the
    # deployer section is a claim about this address, and a provenance verdict
    # printed without its subject cannot be checked by the reader.
    if who:
        line = f"   deployer: {who}"
        outcomes = dep.get("outcomes") or {}
        total, unresolved = outcomes.get("total"), outcomes.get("unresolved")
        if total:
            line += f" — {total:g} prior deployment{'' if total == 1 else 's'}"
            # The unresolved count travels with the total, always. "3 prior
            # deployments" beside a clean-sounding verdict reads as three
            # survivors; it means three contracts whose fate nobody read.
            if unresolved:
                line += f", {unresolved:g} of unknown fate"
        elif total == 0:
            line += " — no prior deployments (a first-timer, not a clean record)"
        lines.append(line)
    lines += [
        "",
        sources_text(result.get("sources")),
    ]
    unused = result.get("unused_fields") or []
    if unused:
        lines.append(f"   ! supplied but unused: {', '.join(unused)} — a source "
                     "is sending fields no check reads")
    return "\n".join(lines)
