"""A capability on the agent card names a module a caller can reach.

``multi_agent_swarm`` sat on ``agent_card.json`` while ``bot/core/swarm.py``
sat on ``tests/unreachable_baseline.txt``. The card told another agent the
swarm exists; the ratchet said nothing imports it. A token in a capability
name that equals an unreachable module stem is that claim.
"""
from __future__ import annotations

import json
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


def test_a_planted_swarm_capability_fails_against_the_real_baseline():
    """The other direction. A clean tree and a rule that cannot see the
    swarm token are the same green, so the token is planted against the
    baseline the file actually holds."""
    stems = unreachable_stems()
    assert "swarm" in stems
    assert collisions(["multi_agent_swarm"]) == {"swarm"}
    assert collisions(["presale_claims"]) == set()


def test_the_readme_does_not_call_the_swarm_production_ready():
    text = README.read_text(encoding="utf-8")
    assert _PRODUCTION_SENTENCE not in text
    assert "unreachable_baseline.txt" in text
