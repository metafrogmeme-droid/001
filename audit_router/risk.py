"""Risk score for routing. A routing score, not a safety verdict.

Signals are read off comment-stripped source. ``scan_security_flags`` still
runs, and its flags are handed to the reviewer as flags. They are not added
again here: delegatecall and selfdestruct are their own routing signals, and
counting the scanner's copy as well would be a second answer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from audit_router.config import RouterConfig
from audit_router.sources import SourceFile, mask_sol

# Chain ids this tree already treats as mainnet in contract constructors
# and chain lists (Ethereum, Base, Optimism, Polygon, BSC, Avalanche,
# Arbitrum, HyperEVM, HashKey, Robinhood).
_MAINNET_IDS = (1, 8453, 10, 137, 56, 43114, 42161, 999, 177, 4663)

_PAYABLE = re.compile(r"\bpayable\b|\breceive\s*\(\s*\)")
_VALUE_CALL = re.compile(r"\.call\s*\{[^}]*\bvalue\b|\.transfer\s*\(|\.send\s*\(")
_APPROVAL = re.compile(r"\bapprove\s*\(|\btransferFrom\s*\(|\bsafeTransfer(?:From)?\s*\(")
_UPGRADE = re.compile(
    r"\bdelegatecall\b|\bselfdestruct\s*\(|\bsuicide\s*\(|\bupgradeTo(?:AndCall)?\s*\("
    r"|\bUUPSUpgradeable\b|\bTransparentUpgradeableProxy\b|\bERC1967\b"
)
_EXTERNAL = re.compile(r"\.(?:call|staticcall)\s*[\({]")
_ORACLE = re.compile(
    r"\blatestRoundData\s*\(|\blatestAnswer\s*\(|\bChainlink\b|\bAggregatorV3\b"
    r"|\bpriceOracle\b|\bIOracle\b"
)
_ACCESS_OZ = re.compile(r"\bAccessControl\b|\bonlyRole\s*\(")
_ROLE = re.compile(r"\bbytes32\s+(?:public\s+|private\s+|internal\s+)?constant\s+(\w*ROLE)\b")
_MODIFIER = re.compile(r"\bmodifier\s+(only[A-Za-z0-9_]+)")
_CONTRACT = re.compile(
    r"^[ \t]*(?:abstract[ \t]+)?contract[ \t]+\w+", re.M)


@dataclass
class Risk:
    """One tree's routing score.

    ``points`` is None when the source could not be read. That is not a
    score of zero, and the tier is high so a funds-moving contract is not
    sent to the cheap model because a read failed.
    """

    points: int | None
    tier: str
    #: None when the source was not read. False is a measurement.
    holds_funds: bool | None
    upgradeable: bool | None
    readable: bool
    signals: list[str] = field(default_factory=list)
    files: int = 0
    code_lines: int = 0
    contracts: int = 0


def _code_lines(masked: str) -> int:
    return sum(1 for line in masked.splitlines() if line.strip())


def _chain_signals(folder: Path | None) -> tuple[list[str], bool]:
    """Chain facts from sibling config. A file that will not parse is
    unreadable, not 'no mainnet'. A folder with no chain file is 'no chain
    file', which is a fact, not a mainnet."""
    if folder is None or not folder.is_dir():
        return ["no chain file"], False
    signals: list[str] = []
    unreadable = False
    networks = folder / "networks.json"
    if networks.is_file():
        try:
            data = json.loads(networks.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            unreadable = True
            signals.append("networks.json unreadable")
        else:
            if isinstance(data, dict) and isinstance(data.get("mainnet"), dict):
                signals.append("mainnet_documented")
    chains = folder / "chains.json"
    if chains.is_file():
        try:
            data = json.loads(chains.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            unreadable = True
            signals.append("chains.json unreadable")
        else:
            if isinstance(data, dict) and data.get("mainnets_refused"):
                signals.append("mainnets_refused")
    foundry = folder / "foundry.toml"
    if foundry.is_file():
        try:
            text = foundry.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            unreadable = True
            signals.append("foundry.toml unreadable")
        else:
            if re.search(r"(?i)mainnet", text) and "mainnet_documented" not in signals:
                signals.append("mainnet_documented")
    if not signals and not unreadable:
        signals.append("no chain file")
    return signals, unreadable


def _source_chain(masked: str) -> str | None:
    ids = [str(i) for i in _MAINNET_IDS if re.search(rf"\b{i}\b", masked)]
    if not ids:
        return None
    if re.search(r"\brevert\b", masked):
        return "mainnet_refused_in_source"
    return "mainnet_target"


def _access_complex(masked: str) -> bool:
    if _ACCESS_OZ.search(masked):
        return True
    if len(set(_ROLE.findall(masked))) >= 2:
        return True
    return len(set(_MODIFIER.findall(masked))) >= 2


def score_sources(files: list[SourceFile], config: RouterConfig,
                  chain_dir: Path | None) -> Risk:
    """Score ``files``. An unreadable file makes the whole score unreadable."""
    if any(not f.readable for f in files) or not files:
        signals = ["source unreadable"] if files else ["no solidity source"]
        return Risk(points=None, tier="high", holds_funds=None, upgradeable=None,
                    readable=False, signals=signals, files=len(files))
    weights = config.weights
    points = 0
    signals: list[str] = []
    holds = False
    upgrade = False
    lines = 0
    contracts = 0

    def add(name: str, weight_key: str) -> None:
        nonlocal points
        if name in signals:
            return
        signals.append(name)
        points += weights[weight_key]

    masked_all: list[str] = []
    for src in files:
        masked = mask_sol(src.text)
        masked_all.append(masked)
        lines += _code_lines(masked)
        contracts += len(_CONTRACT.findall(masked))
        if _PAYABLE.search(masked):
            holds = True
            add("payable", "payable")
        if _VALUE_CALL.search(masked):
            holds = True
            add("value_transfer", "value_transfer")
        if _APPROVAL.search(masked):
            holds = True
            add("token_approval", "token_approval")
        if _UPGRADE.search(masked):
            upgrade = True
            add("upgradeable", "upgradeable")
        if _EXTERNAL.search(masked):
            add("external_call", "external_call")
        if _ORACLE.search(masked):
            add("oracle", "oracle")
        if _access_complex(masked):
            add("access_complex", "access_complex")
        chain = _source_chain(masked)
        if chain == "mainnet_target":
            add("mainnet_target", "mainnet_target")
        elif chain == "mainnet_refused_in_source" and chain not in signals:
            signals.append(chain)

    loc_points = min(weights["loc_cap"], (lines // 100) * weights["per_100_loc"])
    if loc_points:
        points += loc_points
        signals.append(f"code_lines:{lines}")
    extra = max(0, contracts - 1)
    contract_points = min(weights["contract_cap"], extra * weights["per_extra_contract"])
    if contract_points:
        points += contract_points
        signals.append(f"contracts:{contracts}")

    chain_signals, chain_unread = _chain_signals(chain_dir)
    for name in chain_signals:
        if name == "mainnet_documented" and "mainnet_documented" not in signals:
            signals.append(name)
            points += weights["mainnet_documented"]
        elif name not in signals:
            signals.append(name)
    if chain_unread:
        # A chain file we could not read is not "testnet". Route high and
        # say the score is not a measurement of the chain.
        signals.append("chain file unreadable")
        return Risk(points=None, tier="high", holds_funds=holds, upgradeable=upgrade,
                    readable=False, signals=signals, files=len(files),
                    code_lines=lines, contracts=contracts)

    if holds or upgrade or points >= config.high_at:
        tier = "high"
    elif points >= config.low_below:
        tier = "medium"
    else:
        tier = "low"
    return Risk(points=points, tier=tier, holds_funds=holds, upgradeable=upgrade,
                readable=True, signals=signals, files=len(files),
                code_lines=lines, contracts=contracts)
