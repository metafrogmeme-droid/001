"""Markdown report. The verdict is computed here from findings that were
located in the source. It is not copied from the model.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from audit_router.budget import Ledger
from audit_router.review import DISCLAIMER, Finding
from audit_router.risk import Risk
from audit_router.tools import StaticReport
from bot.core.contract_studio import SecurityFlag

_RANK = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1, "Info": 0}


def decide_verdict(findings: list[Finding], *, readable: bool, complete: bool,
                   holds_funds: bool | None, upgradeable: bool | None) -> str:
    """Safe to deploy only when the source was read, the review finished,
    and nothing at Medium or above was found."""
    if not readable or not complete:
        if holds_funds or upgradeable:
            return "Do not deploy"
        return "Fix first"
    rank = 0
    for finding in findings:
        rank = max(rank, _RANK.get(finding.severity, 0))
    if rank >= _RANK["High"]:
        return "Do not deploy"
    if rank >= _RANK["Medium"]:
        return "Fix first"
    return "Safe to deploy"


def report_name(contract: str, models: tuple[str, ...], day: date) -> str:
    slug = re.sub(r"[^A-Za-z0-9._+-]+", "-", contract).strip("-") or "contract"
    model = "+".join(models) if models else "no-model"
    model_slug = re.sub(r"[^A-Za-z0-9._+-]+", "-", model).strip("-")
    return f"{slug}-{model_slug}-{day.isoformat()}.md"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _line(value: int | None) -> str:
    if value is None or value < 1:
        return ""
    return str(value)


def _points(risk: Risk) -> str:
    if risk.points is None:
        return "not scored (the source or a chain file could not be read)"
    return str(risk.points)


def _yes_no(value: bool | None) -> str:
    """None is 'not read'. False is a measurement, not a stand-in for None."""
    if value is None:
        return "not read"
    return "yes" if value else "no"


def _cost(ledger: Ledger) -> str:
    if not ledger.calls:
        return "no model call was made, so there is no cost to report"
    if not ledger.cost_complete:
        return ("incomplete. A call was unmeasured or unpriced, so the "
                "figure below is not a total of the run.")
    return f"{ledger.cost_usd:.6f} USD"


def render(contract: str, risk: Risk, models: tuple[str, ...],
           findings: list[Finding], missing: list[str],
           tests_found: list[str] | None, static: StaticReport | None,
           heuristics: list[tuple[str, SecurityFlag]], ledger: Ledger,
           complete: bool, stopped: str | None) -> str:
    verdict = decide_verdict(
        findings, readable=risk.readable, complete=complete,
        holds_funds=risk.holds_funds, upgradeable=risk.upgradeable,
    )
    lines = [
        f"# Contract audit: {contract}",
        "",
        DISCLAIMER,
        "",
        "## Verdict",
        "",
        f"**{verdict}**",
        "",
        "## Routing",
        "",
        f"- Risk score: {_points(risk)}",
        f"- Tier: {risk.tier}",
        f"- Holds or moves funds: {_yes_no(risk.holds_funds)}",
        f"- Upgradeable or destructive: {_yes_no(risk.upgradeable)}",
        f"- Signals: {', '.join(risk.signals) if risk.signals else 'none'}",
        f"- Code lines: {'not read' if not risk.readable and risk.code_lines == 0 else risk.code_lines}",
        f"- Contracts: {'not read' if not risk.readable and risk.contracts == 0 else risk.contracts}",
        f"- Files read: {risk.files}",
        "- Models: " + (", ".join(models) if models else "none"),
        "",
        "## Severity",
        "",
        "| Severity | File | Line | Finding | Fix | Reported by |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    if not findings:
        lines.append("|  |  |  | No findings were recorded. |  |  |")
    for finding in findings:
        shown = finding.title
        if finding.quote:
            shown = f"{finding.title}: {finding.quote[:160]}"
        lines.append("| " + " | ".join((
            _cell(finding.severity),
            _cell(finding.file),
            _line(finding.line),
            _cell(shown),
            _cell(finding.fix),
            _cell(", ".join(finding.sources)),
        )) + " |")
    lines += ["", "## Heuristic flags", ""]
    if not heuristics:
        lines.append("The contract scanner raised no flags on the source that was read.")
    else:
        lines.append("These are the contract scanner's flags. A flag is not a verdict.")
        lines.append("")
        for rel, flag in heuristics:
            where = str(flag.line) if flag.line else "file-level"
            lines.append(f"- {flag.severity} `{rel}` {where}: {flag.title} {flag.hint}")
    lines += ["", "## Missing tests", ""]
    if tests_found is None:
        lines.append("Test files could not be listed.")
    elif not tests_found:
        lines.append("No test file was found next to the source.")
    else:
        lines.append("Test files present:")
        for path in tests_found:
            lines.append(f"- `{path}`")
    if missing:
        lines.append("")
        lines.append("Gaps named by the reviewer:")
        for item in missing:
            lines.append(f"- {_cell(item)}")
    elif complete:
        lines.append("")
        lines.append("The reviewer named no missing tests.")
    lines += ["", "## Static tools", ""]
    if static is None:
        lines.append("Static tools were not run.")
    else:
        for run in static.runs:
            lines.append(f"- {run.name}: {run.status}. {run.detail}")
        lines.append(f"- {static.fuzz_note}")
    lines += [
        "",
        "## Token use",
        "",
        "| Model | Input | Output | Cache read | Cache write | Cost estimate (USD) | Measured |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    if not ledger.calls:
        lines.append("|  |  |  |  |  |  | no call |")
    for call in ledger.calls:
        lines.append("| " + " | ".join((
            call.model,
            "" if call.tokens_in is None else str(call.tokens_in),
            "" if call.tokens_out is None else str(call.tokens_out),
            str(call.cache_read),
            str(call.cache_write),
            "" if call.cost_usd is None else f"{call.cost_usd:.6f}",
            "yes" if call.measured else "no",
        )) + " |")
    lines += [
        "",
        f"Tokens counted (input, output, and cache): {ledger.tokens}",
        f"Cost estimate: {_cost(ledger)}",
        "The estimate uses bot.core.cost.llm_call_cost and that module's list prices.",
        "It is an estimate, not an invoice.",
        "",
        "## Budget",
        "",
    ]
    if stopped:
        lines.append(f"Hard stop: {stopped}")
        lines.append("The review is incomplete.")
    elif complete:
        lines.append("The run finished inside the token and cost budget.")
    else:
        lines.append("The review did not finish.")
    lines += ["", DISCLAIMER, ""]
    return "\n".join(lines)


def contract_slug(target: Path) -> str:
    name = target.name
    if name.endswith(".sol"):
        name = name[:-4]
    return name or "contract"
