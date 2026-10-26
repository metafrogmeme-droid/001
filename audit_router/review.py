"""The review call. The system prompt is one string for every chunk so
``llm_complete`` can cache that block the way it caches every other system
prompt. This module does not mark the block again.

Line numbers come from searching the quote in the file that was read.
A quote that is not in that file has no line. The model's reply is not
asked for a line number, and one is never taken from it.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from audit_router.budget import estimate_tokens
from audit_router.sources import Chunk
from audit_router.tools import StaticReport, ToolFinding
from bot.core.contract_studio import AUDIT_DISCLAIMER, SecurityFlag
from bot.llm.provider import (
    LLMConfig,
    LLMProvider,
    create_llm_client,
    llm_complete,
    model_supports_structured_output,
    provider_key_env,
)
from bot.utils.secret_shapes import scrub_secrets

DISCLAIMER = "AI-assisted audit, not an external audit."

FINDING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["Critical", "High", "Medium", "Low", "Info"],
                    },
                    "file": {"type": "string"},
                    "quote": {"type": "string"},
                    "title": {"type": "string"},
                    "fix": {"type": "string"},
                },
                "required": ["severity", "file", "quote", "title", "fix"],
            },
        },
        "missing_tests": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["findings", "missing_tests"],
}

_SEVERITY = {
    "critical": "Critical",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
    "info": "Info",
}
_RANK = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1, "Info": 0}


class MissingKey(RuntimeError):
    """The Claude client's key env var is unset. The message names the var."""


@dataclass
class Finding:
    severity: str
    file: str
    line: int | None
    title: str
    fix: str
    quote: str
    sources: list[str] = field(default_factory=list)


def system_prompt() -> str:
    """Byte-stable across chunks and models. The client caches this block."""
    return (
        "You are a Solidity Reviewer. Review only the source in the user "
        "message.\n"
        "Static-tool output and heuristic flags are already in the user "
        "message. Do not re-derive them. Add a finding they missed, or "
        "disagree in the finding title.\n"
        "Put the exact source text in quote. Do not write a line number.\n"
        "Do not provide a ready-to-run exploit. Name the issue and the fix.\n"
        "severity is one of Critical, High, Medium, Low, Info.\n"
        "Reply as JSON with findings and missing_tests. Each finding has "
        "severity, file, quote, title, and fix. missing_tests is an array "
        "of short strings.\n"
        f"{DISCLAIMER}\n"
        f"{AUDIT_DISCLAIMER}\n"
    )


def redact(text: str, key: str) -> str:
    """Drop the live key, then the shared secret vocabulary."""
    if key and text:
        text = text.replace(key, "***REDACTED***")
    return scrub_secrets(text or "")


def _trim(text: str, token_budget: int) -> tuple[str | None, str]:
    cost = estimate_tokens(text)
    if cost is None:
        return None, "left out: its size could not be read"
    if cost <= token_budget:
        return text, ""
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        part = estimate_tokens(text[:mid])
        if part is None:
            return None, "left out: its size could not be read"
        if part <= token_budget:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo], (
        f"cut to the token budget (about {cost} tokens, budget {token_budget}). "
        "The omitted tail was not sent."
    )


def _heuristic_block(flags: list[tuple[str, SecurityFlag]]) -> str:
    if not flags:
        return "Heuristic flags: none."
    lines = ["Heuristic flags (flags, not a verdict):"]
    for rel, flag in flags:
        where = f"line {flag.line}" if flag.line else "file-level"
        lines.append(
            f"- {flag.severity} {rel} {where}: {flag.title}. {flag.hint}"
        )
    return "\n".join(lines)


def _static_block(static: StaticReport) -> str:
    lines = ["Static tools (already run):"]
    for run in static.runs:
        lines.append(f"- {run.name}: {run.status}. {run.detail}")
        for finding in run.findings:
            where = f"line {finding.line}" if finding.line else "no line in the tool output"
            lines.append(
                f"  - {finding.severity} {finding.file} {where}: {finding.title}"
            )
    lines.append(static.fuzz_note)
    return "\n".join(lines)


def user_message(chunk: Chunk, static: StaticReport,
                 flags: list[tuple[str, SecurityFlag]],
                 static_token_budget: int) -> tuple[str | None, str]:
    """The per-chunk user message. None when the static block could not be sized."""
    body, note = _trim(_static_block(static), static_token_budget)
    if body is None:
        return None, note
    text = (
        f"File: {chunk.rel}\n"
        f"Lines {chunk.start_line}-{chunk.end_line} of that file.\n\n"
        f"{body}\n"
        f"{note}\n\n"
        f"{_heuristic_block(flags)}\n\n"
        "Source:\n"
        f"{chunk.text}"
    )
    return text, note


def line_of_quote(source: str, quote: str) -> int | None:
    """The first line where ``quote`` occurs, or None when it does not."""
    if not isinstance(source, str) or not isinstance(quote, str):
        return None
    needle = quote.strip()
    if len(needle) < 8:
        return None
    idx = source.find(needle)
    if idx < 0:
        for line in needle.splitlines():
            part = line.strip()
            if len(part) < 12:
                continue
            idx = source.find(part)
            if idx >= 0:
                break
        else:
            return None
    if idx < 0:
        return None
    return source.count("\n", 0, idx) + 1


def _resolve_file(name: str, files: dict[str, str]) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return None
    raw = name.strip().replace("\\", "/")
    if raw in files:
        return raw
    base = raw.rsplit("/", 1)[-1]
    hits = [rel for rel in files if rel == base or rel.endswith("/" + base)]
    if len(hits) == 1:
        return hits[0]
    return None


def parse_model_findings(text: str, files: dict[str, str], model: str
                         ) -> tuple[list[Finding], list[str], bool]:
    """Findings, missing-test notes, and whether the JSON was readable.

    A line is set only when the quote is found in the named file. The
    reply is not trusted for a number.
    """
    data = _load_json(text)
    if not isinstance(data, dict):
        return [], [], False
    raw_findings = data.get("findings")
    missing = data.get("missing_tests")
    if not isinstance(raw_findings, list) or not isinstance(missing, list):
        return [], [], False
    notes = [str(item).strip() for item in missing if str(item).strip()]
    out: list[Finding] = []
    for item in raw_findings:
        if not isinstance(item, dict):
            return [], [], False
        severity = _SEVERITY.get(str(item.get("severity") or "").strip().lower())
        if severity is None:
            return [], [], False
        quote = str(item.get("quote") or "")
        title = str(item.get("title") or "").strip() or "untitled"
        fix = str(item.get("fix") or "").strip()
        rel = _resolve_file(str(item.get("file") or ""), files)
        line = line_of_quote(files[rel], quote) if rel is not None else None
        out.append(Finding(
            severity, rel or str(item.get("file") or ""), line, title, fix,
            quote.strip(), [model],
        ))
    return out, notes, True


def _load_json(text: str):
    if not isinstance(text, str):
        return None
    body = text.strip()
    if body.startswith("```"):
        lines = body.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        body = "\n".join(lines).strip()
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        start, end = body.find("{"), body.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(body[start:end + 1])
        except json.JSONDecodeError:
            return None


def from_tool(finding: ToolFinding) -> Finding:
    return Finding(
        finding.severity, finding.file, finding.line, finding.title,
        finding.fix, finding.quote, [finding.source],
    )


def _key(finding: Finding) -> tuple:
    quote = re.sub(r"\s+", " ", finding.quote.strip().lower())
    title = re.sub(r"\s+", " ", finding.title.strip().lower())
    return (finding.file, quote or title)


def dedupe(findings: list[Finding]) -> list[Finding]:
    """One row per file and quote. The higher severity is kept."""
    merged: dict[tuple, Finding] = {}
    order: list[tuple] = []
    for finding in findings:
        key = _key(finding)
        have = merged.get(key)
        if have is None:
            merged[key] = Finding(
                finding.severity, finding.file, finding.line, finding.title,
                finding.fix, finding.quote, list(finding.sources),
            )
            order.append(key)
            continue
        if _RANK.get(finding.severity, 0) > _RANK.get(have.severity, 0):
            have.severity = finding.severity
            have.title = finding.title
            have.fix = finding.fix
            if finding.line is not None:
                have.line = finding.line
        for source in finding.sources:
            if source not in have.sources:
                have.sources.append(source)
        if finding.fix and finding.fix not in have.fix:
            have.fix = have.fix + " " + finding.fix if have.fix else finding.fix
    out = [merged[key] for key in order]
    out.sort(key=lambda f: (-_RANK.get(f.severity, 0), f.file, f.line or 10**9))
    return out


async def anthropic_complete(model: str, system: str, user: str, *,
                             max_tokens: int, timeout: float) -> tuple[str, dict]:
    """One Claude call through the existing client. The key stays in the client."""
    env = provider_key_env(LLMProvider.ANTHROPIC)
    key = os.environ.get(env) or ""
    if not key.strip():
        raise MissingKey(f"{env} is not set")
    config = LLMConfig(
        provider=LLMProvider.ANTHROPIC,
        api_key=key,
        model=model,
        max_tokens=max_tokens,
        timeout_seconds=timeout,
    )
    client = create_llm_client(config)
    if client is None:
        raise MissingKey(f"{env} is not set")
    usage: dict = {}
    schema = FINDING_SCHEMA if model_supports_structured_output(model) else None
    text = await llm_complete(
        client, config, system, user,
        json_schema=schema, usage_out=usage, web_search=False,
    )
    return redact(text or "", key), usage
