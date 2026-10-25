"""The server does not sign or broadcast on a mainnet chain id.

On mainnet the server never holds a key and never signs. These tests fail
when a server entry point would start with that door open, when the signer
would sign or broadcast a mainnet chain, or when ``app/`` grows a mainnet
signer. Operator-only ``scripts/anchor_roots.sh --broadcast`` may stay; it
must refuse the server's environment.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
from pathlib import Path

import pytest

from bot.web import web3_signer as signer
from bot.web.mainnet_guard import (
    execution_refused,
    mainnet_chain_ids,
    refuse_server_start,
    startup_refusal,
)
from bot.web.mainnet_guard import testnet_chain_ids as _testnet_chain_ids
from tests.source_scan import code_only

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "anchor_roots.sh"

_SIGNER_MARKERS = (
    "new Wallet(",
    ".signTransaction(",
    "eth_sendRawTransaction",
    "sendRawTransaction(",
)
_CHAIN_LITERAL = re.compile(r"(?:chainId|chain_id)\s*[:=]\s*(\d+)\b")
_JS_QUOTES = "\"'`"


def test_a_known_mainnet_id_is_refused_and_a_known_testnet_id_is_not():
    assert 1 in mainnet_chain_ids()
    assert 8453 in mainnet_chain_ids()
    assert 84532 in _testnet_chain_ids()
    assert 84532 not in mainnet_chain_ids()
    for cid in mainnet_chain_ids():
        assert execution_refused(cid) is not None, cid
    for cid in _testnet_chain_ids():
        assert execution_refused(cid) is None, cid


def test_an_unreadable_chain_id_is_refused():
    """Bool is refused before int(True), which is Ethereum mainnet."""
    assert execution_refused(True) is not None
    assert execution_refused(False) is not None
    assert execution_refused(None) is not None
    assert execution_refused("nope") is not None
    assert execution_refused("") is not None
    assert execution_refused(999999) is not None


async def test_build_and_sign_and_broadcast_still_refuse_mainnet():
    signed = signer.build_and_sign(
        network="ethereum", to="0x" + "11" * 20, value_wei=1,
        nonce=0, gas=21000, env={})
    assert signed["ok"] is False and "testnet" in signed["error"].lower()
    sent = await signer.broadcast("0x02aa", "http://127.0.0.1:1", 1)
    assert sent["ok"] is False and "testnet" in sent["error"].lower()


def test_the_signer_calls_the_shared_refusal():
    """A second check that drifted would let one door open."""
    src = (ROOT / "bot" / "web" / "web3_signer.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in {
            "build_and_sign", "broadcast", "prepare_tx",
        }:
            calls = [
                c.func.id for c in ast.walk(node)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
            ]
            if "execution_refused" in calls:
                found.add(node.name)
    assert found == {"build_and_sign", "broadcast", "prepare_tx"}


def test_startup_refuses_the_mainnet_door_and_allows_a_testnet_key():
    assert startup_refusal({}) is None
    assert startup_refusal({"WEB3_SIGNER_PRIVATE_KEY": "0xabc"}) is None
    allow = startup_refusal({"WEB3_LIVE_EXEC_ALLOW_MAINNET": "true"})
    assert allow is not None and "WEB3_LIVE_EXEC_ALLOW_MAINNET" in allow
    assert "0x" not in allow
    assert startup_refusal({"WEB3_LIVE_EXEC_ALLOW_MAINNET": "false"}) is None
    eth = startup_refusal({"ETH_PRIVATE_KEY": "0xabc"})
    assert eth is not None and "ETH_PRIVATE_KEY" in eth and "0xabc" not in eth
    hl = startup_refusal({"HYPERLIQUID_PRIVATE_KEY": "0xabc"})
    assert hl is not None and "HYPERLIQUID" in hl and "0xabc" not in hl
    assert startup_refusal({
        "HYPERLIQUID_PRIVATE_KEY": "0xabc", "HYPERLIQUID_TESTNET": "1",
    }) is None
    # "on" is not true for _env_bool, so the venue would still be mainnet.
    assert startup_refusal({
        "HYPERLIQUID_PRIVATE_KEY": "0xabc", "HYPERLIQUID_TESTNET": "on",
    }) is not None
    mainnet_chain = startup_refusal({
        "WEB3_SIGNER_PRIVATE_KEY": "0xabc", "CHAIN_ID": "1",
    })
    assert mainnet_chain is not None and "CHAIN_ID" in mainnet_chain
    assert startup_refusal({
        "WEB3_SIGNER_PRIVATE_KEY": "0xabc", "SAR_ORACLE_CHAIN_ID": "84532",
    }) is None
    base = startup_refusal({
        "SAR_ORACLE_SIGNING_KEY": "0xabc", "SAR_ORACLE_CHAIN_ID": "8453",
    })
    assert base is not None and "8453" in base and "0xabc" not in base
    # ONCHAIN_ENABLED=1 is a feature flag whose value is the digit 1.
    # It is not Ethereum.
    assert startup_refusal({
        "WEB3_SIGNER_PRIVATE_KEY": "0xabc", "ONCHAIN_ENABLED": "1",
    }) is None


def test_refuse_server_start_exits_and_marks_the_process(monkeypatch):
    monkeypatch.setenv("RUNECLAW_SERVER", "0")
    with pytest.raises(SystemExit) as caught:
        refuse_server_start({"WEB3_LIVE_EXEC_ALLOW_MAINNET": "1"})
    assert caught.value.code == 1
    assert os.environ["RUNECLAW_SERVER"] == "1"


def test_a_clean_start_still_marks_the_process_as_the_server(monkeypatch):
    monkeypatch.setenv("RUNECLAW_SERVER", "0")
    refuse_server_start({})
    assert os.environ["RUNECLAW_SERVER"] == "1"


def _call_sites(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "refuse_server_start"):
            out.append(node)
    return out


def test_both_server_entry_points_call_the_guard_with_the_process_env():
    main_src = (ROOT / "bot" / "main.py").read_text(encoding="utf-8")
    main_tree = ast.parse(main_src)
    main_fn = next(
        n for n in main_tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "main")
    main_calls = [
        n for n in ast.walk(main_fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "refuse_server_start"
    ]
    assert len(main_calls) == 1 and main_calls[0].args == [] and main_calls[0].keywords == []
    body = ast.get_source_segment(main_src, main_fn) or ""
    assert body.index("refuse_server_start(") < body.index("run_telegram(")

    bridge = (ROOT / "api_bridge.py").read_text(encoding="utf-8")
    bridge_calls = _call_sites(ROOT / "api_bridge.py")
    assert len(bridge_calls) == 1
    assert bridge_calls[0].args == [] and bridge_calls[0].keywords == []
    tail = bridge[bridge.index('if __name__ == "__main__"'):]
    assert tail.index("refuse_server_start(") < tail.index("uvicorn.run(")


def _strip_js_comments(text: str) -> str:
    """Comments out, strings kept. A comment that quotes a signer is not one."""
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in _JS_QUOTES:
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                out.append(text[i])
                if text[i] == "\\":
                    if i + 1 < n:
                        out.append(text[i + 1])
                        i += 2
                        continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i = min(n, i + 2)
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def mainnet_signer_finding(text: str) -> bool:
    code = _strip_js_comments(text)
    if not any(marker in code for marker in _SIGNER_MARKERS):
        return False
    return any(int(m.group(1)) in mainnet_chain_ids() for m in _CHAIN_LITERAL.finditer(code))


def test_a_planted_mainnet_signer_is_caught_and_a_testnet_signer_is_not():
    planted = "const w = new Wallet(key);\nawait w.signTransaction({ chainId: 8453 });\n"
    assert mainnet_signer_finding(planted) is True
    sepolia = "const w = new Wallet(key);\nawait w.signTransaction({ chainId: 84532 });\n"
    assert mainnet_signer_finding(sepolia) is False
    comment = "// new Wallet(key) chainId: 8453\nconst x = 1;\n"
    assert mainnet_signer_finding(comment) is False
    read_only = "const chains = [{ chainId: 1, rpc: 'https://mainnet.example' }];\n"
    assert mainnet_signer_finding(read_only) is False


def _js_tree(root: Path):
    if not root.exists():
        return
    for path in root.rglob("*.js"):
        parts = set(path.parts)
        if "node_modules" in parts or "test" in parts:
            continue
        yield path


def test_app_has_no_mainnet_signer():
    hits = []
    for path in list(_js_tree(ROOT / "app")) + list(_js_tree(ROOT / "tools" / "sar-oracle")):
        if mainnet_signer_finding(path.read_text(encoding="utf-8", errors="replace")):
            hits.append(str(path.relative_to(ROOT)))
    assert hits == [], hits


def test_the_server_tree_does_not_invoke_the_operator_broadcast_script():
    hits = []
    for base in (ROOT / "bot", ROOT / "app"):
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".js", ".sh"}:
                continue
            if "node_modules" in path.parts or "test" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if path.suffix == ".py":
                text = code_only(text)
            elif path.suffix == ".js":
                text = _strip_js_comments(text)
            if "anchor_roots" in text:
                hits.append(str(path.relative_to(ROOT)))
    assert hits == [], hits


def _run_broadcast(extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("RUNECLAW_SERVER", None)
    for name in (
        "WEB3_SIGNER_PRIVATE_KEY", "HYPERLIQUID_PRIVATE_KEY",
        "SAR_ORACLE_SIGNING_KEY", "HRC_FEED_SIGNER_KEY", "HASHKEY_SEAL_SIGNER_KEY",
        "ETH_PRIVATE_KEY",
    ):
        env.pop(name, None)
    env.update(extra)
    return subprocess.run(
        ["bash", str(SCRIPT), "--broadcast", "2026-08-24"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )


def test_broadcast_refuses_the_server_marker():
    done = _run_broadcast({"RUNECLAW_SERVER": "1", "ETH_PRIVATE_KEY": "0xabc"})
    assert done.returncode != 0
    assert "RUNECLAW_SERVER" in done.stderr
    assert "0xabc" not in done.stderr


def test_broadcast_refuses_a_server_signing_key():
    done = _run_broadcast({
        "WEB3_SIGNER_PRIVATE_KEY": "0xabc", "ETH_PRIVATE_KEY": "0xdef",
    })
    assert done.returncode != 0
    assert "WEB3_SIGNER_PRIVATE_KEY" in done.stderr
    assert "0xabc" not in done.stderr and "0xdef" not in done.stderr


def test_the_operator_script_does_not_source_the_server_env():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "source .env" not in text
    assert "source web.env" not in text
    assert ". .env" not in text
    assert "OPERATOR-ONLY" in text
