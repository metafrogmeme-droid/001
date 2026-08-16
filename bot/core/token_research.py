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
* with a key, five of the eight facts get real answers, and the deployer's
  prior contracts come back as ADDRESSES rather than a count — which is what
  made the last column reachable, since you cannot look up the fate of a
  number.

`deployer_fates` then asks a price feed what became of each one. That pass runs
here, not inside a source, because `gather` calls sources independently and none
of them can see another's answer.

WHAT THAT DOES AND DOES NOT UNLOCK

A deployer whose prior tokens are demonstrably still trading can now reach
`clean` — the first input that could ever produce it. What still cannot happen
is `known_bad` from this path: a price feed proves a market ENDED, never that
somebody took it, so the fate pass writes `prior_dead` and never `prior_rugged`,
and dead is scored as a soft ratio with no hard threshold.

The trap the module's own first run produced is still closed. `_outcomes_resolved`
requires that somebody counted the BAD outcomes — `rugged` and `dead` both None
means nobody looked — and still requires a determined fate for half the record,
so nine survivors nobody verified remain UNPROVEN.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from bot.core import token_dossier
from bot.core.deployer_fates import resolve_fates
from bot.core.deployer_history import assess_deployer
from bot.core.deployer_sources import default_deployer_sources
from bot.core.deployer_taint import taint_facts
from bot.core.token_safety import assess_token, coverage, to_veto_features
from bot.core.token_sources import DexScreenerSource, gather
from bot.guardian import integrity_veto

#: Deployer fields that are real output but are not checks — the subject's own
#: address, and the flag saying the history was too long to count. Listed so the
#: `unused_fields` detector does not report them as a misconfigured source; that
#: warning exists to catch a source answering in the wrong dialect, and a false
#: one trains the reader to ignore it.
_DEPLOYER_INFO_FIELDS = frozenset({"deployer_address", "deployments_truncated",
                                   "prior_contracts", "funding_sources",
                                   "runtime_bytecode_hash"})

#: Consumed by `resolve_outcomes` rather than by a named check, so they are read
#: even though no entry in `checks` carries their name.
_DEPLOYER_OUTCOME_FIELDS = frozenset({"prior_deployments", "prior_alive",
                                     "prior_dead"})


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
                      deployer_sources: Optional[Sequence] = None,
                      fate_source: Any = None) -> dict:
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

    # A second pass, because a fate cannot be looked up from a count. The
    # deployer sources return WHICH contracts came before; `deployer_fates`
    # then asks a price feed what became of each. It runs here rather than
    # inside a source because `gather` calls sources independently and none of
    # them can see another's answer.
    deployer_facts = dict(dg["features"])
    fates = None
    if deployer_facts.get("prior_contracts") and fate_source is not False:
        fates = await resolve_fates(deployer_facts["prior_contracts"],
                                    chain, source=fate_source, timeout=timeout)
        # Only the two counts reach the scorer; the per-contract detail is
        # returned for the render. `prior_rugged` is not among them and is not
        # this path's to supply — see deployer_fates' docstring.
        for k in ("prior_alive", "prior_dead"):
            if fates.get(k) is not None:
                deployer_facts[k] = fates[k]

    # The two curated-list lookups. Both are ABSENT unless a list was actually
    # loaded and actually consulted — `taint_facts` omits rather than answering
    # False, because False is a passing check that helps certify a deployer and
    # an empty denylist has certified nothing.
    deployer_facts.update(taint_facts(
        deployer_facts.get("funding_sources"),
        deployer_facts.get("runtime_bytecode_hash")))

    # Scored only when a source actually supplied something. `assess_deployer({})`
    # would return a well-formed report of all-unknowns — "examined, learned
    # nothing" — where the truth is that nothing was examined. Passing None
    # makes the dossier record the section as unread, which is a different
    # sentence and the correct one.
    deployer = assess_deployer(deployer_facts) if deployer_facts else None

    # The Guardian Integrity Veto, in SHADOW: it computes a verdict and nothing
    # acts on it. Wired here because this is the only place its inputs exist —
    # holder concentration, wash-volume shape, listing age, price/liquidity
    # divergence all come out of `token_safety`, and the risk gate the veto's
    # docstring names consumes none of them.
    #
    # Its intended consumer, `meme_executor`, is unwired too (see
    # tests/unreachable_baseline.txt), so ENFORCEMENT is a product decision
    # about the meme path, not a wiring cleanup. This makes the reading exist.
    integrity = integrity_veto.assess(
        to_veto_features(g["features"]), mode="shadow")

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
            "contract": contract, "deployer": deployer, "fates": fates,
            "integrity": integrity,
            "dossier": dossier,
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
        # Per-contract detail under the summary. The counts alone say "2 dead";
        # this says WHICH, and on what reading, so the verdict can be checked
        # rather than believed.
        from bot.core.deployer_fates import human_readable as fates_text
        detail = fates_text(result.get("fates"))
        if detail:
            lines.append(detail)
    # Manipulation shapes, in shadow. `is_reading` is doing real work here:
    # `assess({})` returns the word `clear`, and printing that over zero
    # readable features is a confident all-clear manufactured from no data.
    # Only a verdict resting on something actually read gets shown at all.
    integrity = result.get("integrity")
    if integrity_veto.is_reading(integrity):
        checked, skipped = integrity.get("checked"), integrity.get("skipped")
        line = (f"   integrity (shadow): {str(integrity.get('verdict')).upper()}"
                f" [{checked} of {checked + skipped} shapes readable]")
        for reason in (integrity.get("reasons") or [])[:3]:
            line += f"\n     · {reason}"
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
