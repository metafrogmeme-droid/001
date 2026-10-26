"""One audit run. Read-only: static tools, then the Claude client, then a file."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from audit_router import flag
from audit_router.budget import Ledger, estimate_tokens
from audit_router.config import ConfigError, load_config, models_for
from audit_router.report import contract_slug, decide_verdict, render, report_name
from audit_router.review import (
    Finding,
    MissingKey,
    anthropic_complete,
    dedupe,
    from_tool,
    parse_model_findings,
    redact,
    system_prompt,
    user_message,
)
from audit_router.risk import score_sources
from audit_router.sources import chunk_files, collect_sources, repo_root, resolve_inside_repo
from audit_router.tools import StaticReport, collect_static
from bot.core.contract_studio import SecurityFlag, scan_security_flags
from bot.llm.provider import LLMProvider, provider_key_env

logger = logging.getLogger("audit_router")

_TEST_SUFFIXES = (".t.sol", ".test.js", ".test.mjs")


@dataclass
class RunResult:
    exit_code: int
    report_path: Path | None
    report: str
    verdict: str
    models: tuple[str, ...]
    stopped: str | None
    calls: int


def _tests(target: Path) -> list[str] | None:
    root = repo_root()
    folder = target if target.is_dir() else target.parent
    project = folder.parent if folder.name in {"src", "test", "script"} else folder
    try:
        found: list[str] = []
        for path in sorted(project.rglob("*")):
            if path.is_file() and path.name.endswith(_TEST_SUFFIXES):
                found.append(path.relative_to(root).as_posix())
        return found
    except OSError:
        return None


def _heuristics(files) -> list[tuple[str, SecurityFlag]]:
    out: list[tuple[str, SecurityFlag]] = []
    for src in files:
        if not src.readable:
            continue
        for item in scan_security_flags(src.text):
            out.append((src.rel, item))
    return out


def _key() -> str:
    return os.environ.get(provider_key_env(LLMProvider.ANTHROPIC)) or ""


async def _call(completer, model: str, system: str, user: str, config):
    result = completer(
        model, system, user,
        max_tokens=config.budget.max_output_tokens,
        timeout=config.budget.timeout_seconds,
    )
    if inspect.isawaitable(result):
        return await result
    return result


def run(path: Path | str, *, model: str | None = None,
        config_path: Path | None = None, out_dir: Path | None = None,
        today: date | None = None, completer=None,
        static: StaticReport | None = None, which=None, tool_run=None,
        force_enabled: bool | None = None) -> RunResult:
    """Audit ``path``. ``completer`` replaces the Claude client in tests."""
    on = flag.enabled() if force_enabled is None else force_enabled
    empty = RunResult(2, None, "", "", (), "flag off", 0)
    if not on:
        return empty

    raw = Path(path)
    try:
        target = resolve_inside_repo(raw)
    except ValueError:
        return RunResult(3, None, "", "", (), "path is outside the repository", 0)
    if not target.exists():
        return RunResult(3, None, "", "", (), "path does not exist", 0)

    try:
        config = load_config(config_path)
    except ConfigError as exc:
        return RunResult(3, None, "", "", (), str(exc), 0)

    day = today or datetime.now(timezone.utc).date()
    files, chain_dir = collect_sources(target)
    risk = score_sources(files, config, chain_dir)
    try:
        routed = models_for(risk.tier, config, model)
    except ConfigError as exc:
        return RunResult(3, None, "", "", (), str(exc), 0)

    heuristics = _heuristics(files)
    tests_found = _tests(target)
    if static is None and risk.readable:
        static = collect_static(
            target, timeout=config.budget.timeout_seconds,
            which=which, run=tool_run)
    if static is None:
        static = StaticReport([], "static tools were not run")

    ledger = Ledger(config.budget.max_tokens, config.budget.max_cost_usd)
    findings: list[Finding] = [from_tool(item) for item in static.findings]
    missing: list[str] = []
    stopped: str | None = None
    complete = False
    exit_code = 0
    system = system_prompt()
    filemap = {src.rel: src.text for src in files if src.readable}
    caller = completer or anthropic_complete

    async def _review() -> None:
        nonlocal stopped, complete, exit_code
        if not risk.readable:
            stopped = "source could not be read"
            exit_code = 5
            return
        chunks = chunk_files(files, config.budget.chunk_input_tokens, estimate_tokens)
        if chunks is None:
            stopped = "token estimate unreadable"
            exit_code = 5
            return
        for mid in routed:
            if stopped:
                break
            for chunk in chunks:
                if stopped:
                    break
                user, note = user_message(
                    chunk, static, heuristics, config.budget.chunk_input_tokens)
                if user is None:
                    stopped = note
                    exit_code = 5
                    return
                est_system = estimate_tokens(system)
                est_user = estimate_tokens(user)
                if est_system is None or est_user is None:
                    stopped = "token estimate unreadable"
                    exit_code = 5
                    return
                reason = ledger.allow(mid, est_system + est_user,
                                      config.budget.max_output_tokens)
                if reason:
                    stopped = reason
                    exit_code = 4
                    return
                try:
                    text, usage = await _call(caller, mid, system, user, config)
                except MissingKey as exc:
                    stopped = str(exc)
                    exit_code = 5
                    return
                except Exception as exc:
                    logger.info(
                        "audit_router model call failed (%s)", type(exc).__name__)
                    stopped = f"model call failed ({type(exc).__name__})"
                    exit_code = 5
                    return
                text = redact(text or "", _key())
                ledger.record(mid, usage if isinstance(usage, dict) else None,
                              config.budget.max_output_tokens)
                if ledger.stopped and "unmeasured" in (ledger.stopped or ""):
                    stopped = ledger.stopped
                    exit_code = 5
                    return
                if usage and usage.get("cut"):
                    stopped = ledger.stopped or "model output was cut"
                    exit_code = 5
                    return
                parsed, notes, ok = parse_model_findings(text, filemap, mid)
                if not ok:
                    stopped = "model output was not readable JSON"
                    exit_code = 5
                    return
                findings.extend(parsed)
                for note_item in notes:
                    if note_item not in missing:
                        missing.append(note_item)
                if ledger.stopped:
                    stopped = ledger.stopped
                    exit_code = 4
                    return
        complete = stopped is None
        if complete:
            exit_code = 0

    asyncio.run(_review())
    merged = dedupe(findings)
    body = render(
        contract_slug(target), risk, routed, merged, missing, tests_found,
        static, heuristics, ledger, complete, stopped,
    )
    body = redact(body, _key())
    out = out_dir or (repo_root() / "docs" / "audits")
    try:
        out.mkdir(parents=True, exist_ok=True)
        dest = out / report_name(contract_slug(target), routed, day)
        dest.write_text(body, encoding="utf-8")
    except OSError as exc:
        logger.info("audit_router report write failed (%s)", type(exc).__name__)
        return RunResult(5, None, body, "", routed, stopped, len(ledger.calls))
    verdict = decide_verdict(
        merged, readable=risk.readable, complete=complete,
        holds_funds=risk.holds_funds, upgradeable=risk.upgradeable,
    )
    return RunResult(exit_code, dest, body, verdict, routed, stopped, len(ledger.calls))
