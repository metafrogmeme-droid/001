"""A capability on the agent card names a module a caller can reach.

``multi_agent_swarm`` sat on ``agent_card.json`` while ``bot/core/swarm.py``
sat on ``tests/unreachable_baseline.txt`` (it has since been wired in behind a
default-off flag, and the card still does not claim it). The card told another agent the
swarm exists; the ratchet said nothing imports it. A token in a capability
name that equals an unreachable module stem is that claim.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CARD = ROOT / "agent_card.json"
BASELINE = ROOT / "tests" / "unreachable_baseline.txt"
README = ROOT / "README.md"

#: The sentence the card's neighbour used to print. It lives here so the
#: README check has a literal to look for; the README itself must not.
_PRODUCTION_SENTENCE = (
    "Ready for production deployment as separate Agent Hub agents."
)


def unreachable_stems() -> set[str]:
    stems: set[str] = set()
    for line in BASELINE.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        stems.add(Path(text).stem)
    return stems


def capability_tokens(capabilities) -> set[str]:
    tokens: set[str] = set()
    for cap in capabilities:
        for part in str(cap).split("_"):
            if part:
                tokens.add(part)
    return tokens


def collisions(capabilities) -> set[str]:
    return capability_tokens(capabilities) & unreachable_stems()


def test_no_capability_names_an_unreachable_module():
    card = json.loads(CARD.read_text(encoding="utf-8"))
    hit = collisions(card["capabilities"])
    assert hit == set(), (
        f"capability tokens name unreachable modules: {sorted(hit)}"
    )


def test_the_swarm_is_reachable_so_it_is_no_longer_on_the_baseline():
    """`bot/core/swarm.py` left `unreachable_baseline.txt` when the engine
    started importing it (an optional coordinator, `SWARM_ENABLED`, off by
    default). The baseline test for it is the module ratchet; this pins the
    importer, so the entry cannot come back unnoticed or the import go."""
    assert "swarm" not in unreachable_stems()
    engine_source = (ROOT / "bot" / "core" / "engine.py").read_text(encoding="utf-8")
    assert "from bot.core.swarm import SwarmCoordinator" in engine_source


def test_a_planted_capability_fails_against_a_baseline_that_holds_the_module(
        tmp_path, monkeypatch):
    """The other direction. A clean tree and a rule that cannot see a token
    are the same green, so a token is planted. The real baseline now holds one
    module, `presale_claims`, whose stem has an underscore and so can never be
    one capability word; the rule is therefore driven against a baseline in the
    shape the real one had while the swarm was on it."""
    planted = tmp_path / "unreachable_baseline.txt"
    planted.write_text("# a keep\nbot/core/swarm.py\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "BASELINE", planted)
    assert unreachable_stems() == {"swarm"}
    assert collisions(["multi_agent_swarm"]) == {"swarm"}
    assert collisions(["presale_claims"]) == set()


def test_the_readme_does_not_call_the_swarm_production_ready():
    text = README.read_text(encoding="utf-8")
    assert _PRODUCTION_SENTENCE not in text
    # It is said to be optional and off by default, in the swarm's own section.
    section = text.split("### Multi-Agent Swarm", 1)[1].split("###", 1)[0]
    assert "SWARM_ENABLED" in section and "off by default" in section
    assert "not a production MCP deployment" in section


def test_the_card_does_not_claim_the_swarm_while_it_is_off_by_default():
    """The card is an advertisement. The swarm is reachable now, which lets a
    token pass the collision check above; it does not make it a default
    capability."""
    card = json.loads(CARD.read_text(encoding="utf-8"))
    assert "swarm" not in capability_tokens(card["capabilities"])
