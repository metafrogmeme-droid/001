"""Slither, Aderyn, and ``forge test``.

Only those three commands run, and forge is only ``forge test``. A tool
that is not installed is reported as not installed. A clean bill is not
invented for it. Nothing here broadcasts or signs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from audit_router.sources import repo_root, resolve_inside_repo

_SECRET_ENV = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)", re.I)
_IMPACT = {
    "critical": "Critical",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
    "informational": "Info",
    "info": "Info",
    "optimization": "Info",
}
_FORGE = ("forge", "test")


@dataclass
class ToolFinding:
    severity: str
    file: str
    line: int | None
    title: str
    fix: str
    quote: str
    source: str


@dataclass
class ToolRun:
    name: str
    status: str
    detail: str
    findings: list[ToolFinding] = field(default_factory=list)


@dataclass
class StaticReport:
    runs: list[ToolRun]
    fuzz_note: str

    @property
    def findings(self) -> list[ToolFinding]:
        out: list[ToolFinding] = []
        for run in self.runs:
            out.extend(run.findings)
        return out


def tool_env() -> dict[str, str]:
    """Subprocess environment with signing and API credentials removed."""
    return {k: v for k, v in os.environ.items() if not _SECRET_ENV.search(k)}


def foundry_root(start: Path) -> Path | None:
    root = repo_root()
    cur = start if start.is_dir() else start.parent
    while True:
        if (cur / "foundry.toml").is_file():
            return cur
        if cur == root or cur.parent == cur:
            return None
        cur = cur.parent


def fuzz_note(foundry: Path | None) -> str:
    if foundry is None:
        return "no foundry.toml beside the source, so forge test was not run"
    path = foundry / "foundry.toml"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "foundry.toml could not be read, so its fuzz setting is unknown"
    if not re.search(r"(?im)^\[profile\.[^\]]*\.fuzz\]|^fuzz\s*=", text):
        return "foundry.toml has no fuzz section; forge test still runs the suite"
    runs = re.search(r"(?m)^runs\s*=\s*(\d+)\s*$", text)
    if runs:
        return (f"foundry.toml sets fuzz runs = {runs.group(1)}; "
                "forge test includes those runs")
    return "foundry.toml has a fuzz section; forge test includes it"


def _positive_line(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _slither_findings(payload) -> list[ToolFinding] | None:
    if not isinstance(payload, dict):
        return None
    results = payload.get("results")
    if not isinstance(results, dict):
        return None
    detectors = results.get("detectors")
    if not isinstance(detectors, list):
        return None
    out: list[ToolFinding] = []
    for det in detectors:
        if not isinstance(det, dict):
            continue
        impact = str(det.get("impact") or "")
        severity = _IMPACT.get(impact.strip().lower())
        if severity is None:
            severity = "Info"
            title_impact = impact or "impact not in the slither output"
        else:
            title_impact = impact
        check = str(det.get("check") or "slither")
        desc = str(det.get("description") or "").strip().splitlines()
        title = desc[0][:180] if desc else check
        elements = det.get("elements") if isinstance(det.get("elements"), list) else []
        file = ""
        line = None
        if elements and isinstance(elements[0], dict):
            mapping = elements[0].get("source_mapping")
            if isinstance(mapping, dict):
                rel = mapping.get("filename_relative") or mapping.get("filename_absolute")
                if isinstance(rel, str):
                    file = rel
                lines = mapping.get("lines")
                if isinstance(lines, list) and lines:
                    line = _positive_line(lines[0])
        out.append(ToolFinding(
            severity, file, line, f"{check}: {title}",
            "See the detector and change the cited code.",
            "", "slither",
        ))
        if title_impact and severity == "Info" and impact.strip().lower() not in _IMPACT:
            out[-1].title = f"{check}: {title} ({title_impact})"
    return out


def _aderyn_bucket(payload: dict, key: str, severity: str) -> list[ToolFinding]:
    bucket = payload.get(key)
    if isinstance(bucket, dict):
        issues = bucket.get("issues")
    else:
        issues = bucket
    if not isinstance(issues, list):
        return []
    out: list[ToolFinding] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        title = str(issue.get("title") or "aderyn")
        instances = issue.get("instances")
        if not isinstance(instances, list) or not instances:
            out.append(ToolFinding(
                severity, "", None, title,
                str(issue.get("description") or "Review the aderyn finding.")[:300],
                "", "aderyn",
            ))
            continue
        for inst in instances:
            if not isinstance(inst, dict):
                continue
            path = inst.get("contract_path") or inst.get("file") or ""
            line = _positive_line(inst.get("line_no") or inst.get("line"))
            out.append(ToolFinding(
                severity, str(path), line, title,
                str(issue.get("description") or "Review the aderyn finding.")[:300],
                "", "aderyn",
            ))
    return out


def _aderyn_findings(payload) -> list[ToolFinding] | None:
    if not isinstance(payload, dict):
        return None
    keys = ("high_issues", "medium_issues", "low_issues", "high", "medium", "low")
    if not any(k in payload for k in keys):
        return None
    out: list[ToolFinding] = []
    out.extend(_aderyn_bucket(payload, "high_issues", "High"))
    out.extend(_aderyn_bucket(payload, "high", "High"))
    out.extend(_aderyn_bucket(payload, "medium_issues", "Medium"))
    out.extend(_aderyn_bucket(payload, "medium", "Medium"))
    out.extend(_aderyn_bucket(payload, "low_issues", "Low"))
    out.extend(_aderyn_bucket(payload, "low", "Low"))
    return out


def _parse_json(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _forge_detail(returncode: int, stdout: str) -> tuple[str, str]:
    """(status, detail). A pass count is reported only when the output has one."""
    match = re.search(r"(\d+)\s+passed", stdout)
    if returncode == 0:
        if match:
            return "passed", f"forge test exited 0; {match.group(1)} passed"
        return "passed", "forge test exited 0; the output did not include a pass count"
    if match:
        failed = re.search(r"(\d+)\s+failed", stdout)
        extra = f"; {failed.group(1)} failed" if failed else ""
        return "failed", f"forge test exited {returncode}; {match.group(1)} passed{extra}"
    return "failed", f"forge test exited {returncode}"


def _clip(text: str, limit: int = 1500) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n… output cut"


@dataclass
class _Proc:
    status: str
    stdout: str
    detail: str
    code: int | None


def default_which(name: str) -> str | None:
    return shutil.which(name)


def default_run(argv: list[str], cwd: Path, env: dict, timeout: float):
    if not argv or argv[0] not in {"slither", "aderyn", "forge"}:
        raise RuntimeError("refused command")
    if argv[0] == "forge" and tuple(argv) != _FORGE:
        raise RuntimeError("refused forge command")
    return subprocess.run(
        argv, cwd=str(cwd), env=env, timeout=timeout,
        capture_output=True, text=True, check=False,
    )


def _execute(name: str, argv: list[str], cwd: Path, env: dict, timeout: float,
             run) -> _Proc:
    try:
        completed = run(argv, cwd, env, timeout)
    except subprocess.TimeoutExpired:
        return _Proc("timed_out", "", f"{name} timed out", None)
    except (OSError, RuntimeError) as exc:
        return _Proc("failed", "", f"{name} could not be run ({type(exc).__name__})", None)
    stdout = getattr(completed, "stdout", "") or ""
    stderr = getattr(completed, "stderr", "") or ""
    code = getattr(completed, "returncode", None)
    if not isinstance(code, int):
        return _Proc("unreadable", stdout, f"{name} returned no exit code", None)
    detail = _clip(stdout + ("\n" + stderr if stderr else ""))
    return _Proc("ran", stdout, detail, code)


def collect_static(target: Path, *, timeout: float, which=None, run=None) -> StaticReport:
    """Run the tools that are on PATH. Never raises for a missing tool."""
    which = which or default_which
    run = run or default_run
    target = resolve_inside_repo(target)
    foundry = foundry_root(target)
    project = foundry or (target if target.is_dir() else target.parent)
    note = fuzz_note(foundry)
    runs: list[ToolRun] = []
    env = tool_env()

    if which("slither"):
        proc = _execute("slither", ["slither", str(project), "--json", "-"],
                        project, env, timeout, run)
        findings: list[ToolFinding] = []
        status, detail = proc.status, proc.detail
        if proc.status == "ran":
            parsed = _slither_findings(_parse_json(proc.stdout))
            if parsed is None:
                status = "unreadable"
                detail = "slither ran but its JSON was not readable"
            else:
                findings = parsed
                status = "ok"
                detail = f"slither reported {len(findings)} detector hits"
                if proc.code not in (0, None) and not findings:
                    detail = f"slither exited {proc.code} and reported no detectors"
        runs.append(ToolRun("slither", status, detail, findings))
    else:
        runs.append(ToolRun("slither", "not_installed", "slither is not on PATH"))

    if which("aderyn"):
        with tempfile.TemporaryDirectory(prefix="audit-router-") as tmp:
            out_path = str(Path(tmp) / "aderyn.json")
            proc = _execute(
                "aderyn", ["aderyn", "--output", out_path, str(project)],
                project, env, timeout, run)
            findings = []
            status, detail = proc.status, proc.detail
            if proc.status == "ran":
                try:
                    raw = Path(out_path).read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    raw = ""
                parsed = _aderyn_findings(_parse_json(raw))
                if parsed is None:
                    status = "unreadable"
                    detail = "aderyn ran but its JSON was not readable"
                else:
                    findings = parsed
                    status = "ok"
                    detail = f"aderyn reported {len(findings)} instances"
                    if proc.code not in (0, None) and not findings:
                        detail = f"aderyn exited {proc.code} and reported no instances"
            runs.append(ToolRun("aderyn", status, detail, findings))
    else:
        runs.append(ToolRun("aderyn", "not_installed", "aderyn is not on PATH"))

    if foundry is None:
        runs.append(ToolRun("forge", "not_a_foundry_project", note))
    elif which("forge"):
        proc = _execute("forge", ["forge", "test"], foundry, env, timeout, run)
        findings = []
        status, detail = proc.status, proc.detail
        if proc.status == "ran" and proc.code is not None:
            kind, summary = _forge_detail(proc.code, proc.stdout)
            status = kind
            detail = summary + ". " + note
            if kind == "failed":
                findings.append(ToolFinding(
                    "High", foundry.relative_to(repo_root()).as_posix(), None,
                    "forge test failed",
                    "Fix the failing test. The router does not treat a failed suite as a pass.",
                    "", "forge",
                ))
        elif proc.status == "ran":
            status = "unreadable"
            detail = "forge test returned no exit code"
        runs.append(ToolRun("forge", status, detail, findings))
    else:
        runs.append(ToolRun(
            "forge", "not_installed", "forge is not on PATH. " + note))
    return StaticReport(runs, note)
