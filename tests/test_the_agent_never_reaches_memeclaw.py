"""The agent never launches, buys or sells a Memeclaw coin.

The owner lifted "never launches tokens" on 21 October 2026 for one thing
only: a person launching a coin from their own wallet on the DogeOS testnet
(``/memeclaw``, ``docs/memeclaw/MEMECLAW_PLAN.md``). The agent keeps the rule.
This holds the line structurally:

- no module the bot runs names Memeclaw, its routes, its contract or the
  DogeOS testnet (comments and docstrings do not count: ``code_only``);
- the bot's own on-chain signer (``bot/web/web3_exec_gate.NETWORKS``, the
  operator-key path that can deploy contracts) has no DogeOS network, so it
  could not deploy the factory or send a launch even if asked;
- the testnet the contract is pinned to is the one the website's registry
  calls a testnet.

A scan cannot see reachability, so this is the shape a test cannot drive: a
path that would exist only if someone wrote it.
"""

import json
import re
from pathlib import Path

from tests.source_scan import code_only

ROOT = Path(__file__).resolve().parent.parent
DOGEOS_TESTNET_CHAIN_ID = 6281971

#: What a bot module would have to say to reach Memeclaw.
REACH = re.compile(
    r"memeclaw|/api/memeclaw|dogeos|rpc\.testnet\.dogeos\.com|" + str(DOGEOS_TESTNET_CHAIN_ID)
    + r"|0x5fdaf3",
    re.IGNORECASE,
)


def _bot_sources():
    for path in sorted((ROOT / "bot").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def test_no_bot_module_names_memeclaw_its_routes_or_its_chain():
    hits = []
    for path in _bot_sources():
        code = code_only(path.read_text(encoding="utf-8"))
        for m in REACH.finditer(code):
            line = code.count("\n", 0, m.start()) + 1
            hits.append(f"{path.relative_to(ROOT)}:{line}: {m.group(0)}")
    assert hits == [], "the agent must have no path to Memeclaw:\n  " + "\n  ".join(hits)


def test_the_scan_would_see_a_path_if_one_were_written():
    # The other arm: the pattern matches each way a bot module could reach it.
    for reach in ('requests.post("https://x/api/memeclaw/launch-plan")', "chain_id = 6281971",
                  'NETWORKS["dogeos-testnet"]', 'hex(0x5fdaf3)', "from app import Memeclaw"):
        assert REACH.search(code_only(reach)), reach
    # and a comment saying the word is not a path.
    assert not REACH.search(code_only("x = 1  # memeclaw is not ours\n"))


def test_the_bots_signer_has_no_dogeos_network():
    from bot.web import web3_exec_gate

    ids = {int(n["chain_id"]) for n in web3_exec_gate.NETWORKS.values()}
    keys = {k.lower() for k in web3_exec_gate.NETWORKS}
    assert DOGEOS_TESTNET_CHAIN_ID not in ids
    assert not any("doge" in k for k in keys)


def test_the_contract_is_pinned_to_the_registrys_testnet():
    registry = (ROOT / "app" / "lib" / "root_anchor.js").read_text(encoding="utf-8")
    m = re.search(r"'dogeos-testnet':\s*\{[^}]*chain_id:\s*(\d+)[^}]*testnet:\s*(true|false)", registry)
    assert m, "the registry no longer describes the DogeOS testnet"
    assert int(m.group(1)) == DOGEOS_TESTNET_CHAIN_ID and m.group(2) == "true"
    source = (ROOT / "contracts" / "memeclaw" / "MemeclawFactory.sol").read_text(encoding="utf-8")
    assert re.search(r"CHAIN_ID\s*=\s*%d;" % DOGEOS_TESTNET_CHAIN_ID, source)
    assert 'require(block.chainid == CHAIN_ID, "testnet only")' in source
    # The artifact the website carries is built from that source.
    artifact = json.loads((ROOT / "app" / "content" / "memeclaw" / "MemeclawFactory.json").read_text())
    import hashlib

    assert artifact["source_sha256"] == hashlib.sha256(source.encode("utf-8")).hexdigest()
