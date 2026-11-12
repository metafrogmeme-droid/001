#!/usr/bin/env python3
"""Is the bot up, and why not? One read-only report.

    python3 scripts/bot_status.py              # everything, last 40 log lines per source
    python3 scripts/bot_status.py --lines 100
    python3 scripts/bot_status.py --log /path/to/extra.log

READ-ONLY. This script restarts nothing, stops nothing, signals nothing and
writes no file. It runs the read-only checks the repository already has and
adds what they leave out, so a person (or the bot-status skill,
``.claude/skills/bot-status/SKILL.md``) can decide what to do. Recovery is the
supervisor's job (``scripts/systemd/``, ``Restart=always``), and a kill and
relaunch by hand next to a supervisor starts a second bot. See
``docs/BOT_STATUS_RUNBOOK.md``.

Four sections:

1. **Supervisor**: ``scripts/systemd/runeclaw-status.sh`` as it stands
   (systemd state, restart counts, the crashloop flag), its exit read as
   0 healthy, 1 something wrong, 3 could not tell.
2. **Processes**: how many of each entry point are running and for how long.
   More than one ``bot.main`` is flagged: two bots poll one Telegram token and
   write one set of state files. Command lines are not printed.
3. **Ports**: one GET to each health endpoint, no header sent. Any HTTP status
   is an answer (the gateway answers 403 without its secret); a refused or
   timed-out connection is not.
4. **Logs**: the last lines of the journal for each installed unit, and of the
   launcher's log files, every line through ``bot.utils.secret_shapes`` (secret
   shapes redacted, URL queries dropped) before it is printed. The ``.env``
   file is never opened.

Three outcomes, as ``runeclaw-status.sh`` has them: exit 0 when every check ran
and found nothing wrong, 1 when something is wrong, 3 when a check could not
run and nothing was found wrong. A check that could not run is said in those
words, never shown as a pass.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK, WRONG, UNKNOWN = 0, 1, 3

#: Entry points (CLAUDE.md "Deploying and verifying"): pattern, label,
#: whether it always runs. The optional bots run only where their tokens are.
ENTRY_POINTS = (
    (r"bot\.main", "bot (bot.main)", True),
    (r"api_bridge", "api bridge (api_bridge.py)", True),
    (r"bot\.duel_bot", "Daily Duel bot (bot.duel_bot)", False),
    (r"bot\.daily_duel_bot", "anonymous Daily Duel bot (bot.daily_duel_bot)", False),
)
#: Health endpoints, as runeclaw-status.sh probes them.
PROBES = (
    ("bot gateway", "http://127.0.0.1:8080/gateway/health", True),
    ("api bridge", "http://127.0.0.1:8000/health", True),
    ("Daily Duel bot", "http://127.0.0.1:8081/health", False),
    ("anonymous Daily Duel bot", "http://127.0.0.1:8082/health", False),
)
UNITS = ("runeclaw-bot", "runeclaw-bridge", "runeclaw-duel", "runeclaw-daily-duel")
#: The launcher's log files (scripts/launch_all.sh.template), relative to the checkout.
LOG_FILES = ("bot.log", "api_bridge.log", "duel_bot.log", "daily_duel_bot.log", "logs/bot.log")
MAX_LINES = 200
ERRORISH = ("Traceback", "ERROR", "CRITICAL", "Exception", "FATAL")

Runner = Callable[[Sequence[str]], tuple[int, str]]


def run(cmd: Sequence[str], timeout: float = 20.0) -> tuple[int, str]:
    """(exit code, stdout+stderr) of a read-only command; 127 when it is not there."""
    if shutil.which(cmd[0]) is None and not Path(cmd[0]).is_file():
        return 127, f"{cmd[0]}: not available"
    try:
        p = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]}: timed out after {timeout:.0f}s"
    except OSError as exc:
        return 126, f"{cmd[0]}: {type(exc).__name__}"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def scrub(text: str) -> str:
    """Secret shapes redacted and URL queries dropped, line by line."""
    from bot.utils.secret_shapes import drop_url_queries, scrub_secrets
    return "\n".join(drop_url_queries(scrub_secrets(line)) for line in text.splitlines())


@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    worst: int = OK

    def say(self, text: str = "") -> None:
        self.lines.append(text)

    def mark(self, outcome: int) -> None:
        # A failure is a verdict; "could not tell" never softens it.
        if outcome == WRONG or (outcome == UNKNOWN and self.worst == OK):
            self.worst = outcome


# ── 1. supervisor ──────────────────────────────────────────────────────────

def supervisor(rep: Report, root: Path, runner: Runner) -> None:
    rep.say("== Supervisor (scripts/systemd/runeclaw-status.sh)")
    script = root / "scripts/systemd/runeclaw-status.sh"
    if not script.is_file():
        rep.say("UNKNOWN: runeclaw-status.sh is not in this checkout; supervision was not checked.")
        rep.mark(UNKNOWN)
        return
    code, out = runner(["bash", str(script)])
    for line in scrub(out).splitlines():
        rep.say("  " + line)
    meaning = {0: "healthy", 1: "something is wrong (above)", 3: "could not tell (above)"}
    rep.say(f"  -> exit {code}: {meaning.get(code, 'unexpected exit; read the lines above')}")
    rep.mark(code if code in (OK, WRONG, UNKNOWN) else UNKNOWN)


# ── 2. processes ───────────────────────────────────────────────────────────

def processes(rep: Report, runner: Runner, own_pid: int) -> None:
    rep.say("== Processes")
    for pattern, label, always in ENTRY_POINTS:
        code, out = runner(["pgrep", "-f", pattern])
        if code == 127:
            rep.say(f"UNKNOWN  {label}: pgrep is not available")
            rep.mark(UNKNOWN)
            continue
        if code not in (0, 1):
            rep.say(f"UNKNOWN  {label}: pgrep exited {code}")
            rep.mark(UNKNOWN)
            continue
        pids = [p for p in out.split() if p.isdigit() and int(p) != own_pid]
        pids = [p for p in pids if not _is_self(runner, p)]
        if not pids:
            if always:
                rep.say(f"DOWN     {label}: not running")
                rep.mark(WRONG)
            else:
                rep.say(f"absent   {label}: not running (optional; runs only where its token is set)")
            continue
        ages = [_age(runner, p) for p in pids]
        shown = ", ".join(f"pid {p} up {a}" for p, a in zip(pids, ages))
        if pattern == r"bot\.main" and len(pids) > 1:
            rep.say(f"TWO+     {label}: {len(pids)} running ({shown}). Two bots poll one Telegram "
                    "token and write one set of state files; find out which one the supervisor owns.")
            rep.mark(WRONG)
        else:
            rep.say(f"running  {label}: {shown}")


def _is_self(runner: Runner, pid: str) -> bool:
    """pgrep -f matches whole command lines, and this script's own children
    (and this script) can contain a pattern. Only the program name is read."""
    code, out = runner(["ps", "-o", "args=", "-p", pid])
    return code == 0 and "bot_status.py" in out


def _age(runner: Runner, pid: str) -> str:
    code, out = runner(["ps", "-o", "etimes=", "-p", pid])
    s = out.strip()
    if code != 0 or not s.isdigit():
        return "unknown"
    secs = int(s)
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return f"{d}d{h:02d}h{m:02d}m" if d else f"{h}h{m:02d}m"


# ── 3. ports ───────────────────────────────────────────────────────────────

def probe_url(url: str, timeout: float = 4.0) -> Optional[int]:
    """The HTTP status the endpoint answered, or None when nothing answered.
    A GET with no header: no secret is sent, and a 403 is an answer."""
    req = urllib.request.Request(url, headers={"User-Agent": "bot-status"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(r.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def ports(rep: Report, prober: Callable[[str], Optional[int]]) -> None:
    rep.say("== Ports")
    for label, url, always in PROBES:
        code = prober(url)
        if code is not None:
            rep.say(f"answering      {label}: HTTP {code} ({url})")
        elif always:
            rep.say(f"NOT ANSWERING  {label} ({url})")
            rep.mark(WRONG)
        else:
            rep.say(f"not answering  {label} (optional) ({url})")


# ── 4. logs ────────────────────────────────────────────────────────────────

def tail_file(path: Path, n: int) -> list[str]:
    """The last ``n`` lines, read from the end without loading the file."""
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        end = fh.tell()
        block, data = 64 * 1024, b""
        while end > 0 and data.count(b"\n") <= n:
            start = max(0, end - block)
            fh.seek(start)
            data = fh.read(end - start) + data
            end = start
    return data.decode("utf-8", errors="replace").splitlines()[-n:]


def _summarise(rep: Report, lines: list[str]) -> None:
    errs = [ln for ln in lines if any(w in ln for w in ERRORISH)]
    if errs:
        rep.say(f"  ({len(errs)} error-looking line(s) in this tail; the last:)")
        rep.say("  >> " + errs[-1].strip()[:300])
    for ln in lines:
        rep.say("  | " + ln[:400])


def logs(rep: Report, root: Path, runner: Runner, n: int, extra: Sequence[Path]) -> None:
    rep.say(f"== Logs (last {n} lines per source, secrets scrubbed)")
    shown = 0
    for unit in UNITS:
        for scope in ([], ["--user"]):
            code, out = runner(["journalctl", *scope, "-u", unit, "-n", str(n), "--no-pager", "-q"])
            text = out.strip()
            if code != 0 or not text or text.startswith("-- No entries --"):
                continue
            rep.say(f"-- journal {' '.join(scope + [unit])}")
            _summarise(rep, scrub(text).splitlines())
            shown += 1
            break
    for rel in LOG_FILES:
        p = root / rel
        if p.is_file():
            rep.say(f"-- {rel}")
            _summarise(rep, scrub("\n".join(tail_file(p, n))).splitlines())
            shown += 1
    for p in extra:
        if p.name == ".env" or p.name.endswith(".env"):
            rep.say(f"-- {p}: refused (an env file holds secrets; it is never read)")
            continue
        if p.is_file():
            rep.say(f"-- {p}")
            _summarise(rep, scrub("\n".join(tail_file(p, n))).splitlines())
            shown += 1
        else:
            rep.say(f"-- {p}: not a file")
    if not shown:
        rep.say("UNKNOWN: no journal entries and no launcher log file were found; nothing was read.")
        rep.mark(UNKNOWN)


def build(root: Path, runner: Runner = run, prober: Callable[[str], Optional[int]] = probe_url,
          lines: int = 40, extra: Sequence[Path] = (), own_pid: Optional[int] = None) -> Report:
    rep = Report()
    n = max(1, min(int(lines), MAX_LINES))
    supervisor(rep, root, runner)
    rep.say()
    processes(rep, runner, os.getpid() if own_pid is None else own_pid)
    rep.say()
    ports(rep, prober)
    rep.say()
    logs(rep, root, runner, n, extra)
    rep.say()
    rep.say({OK: "VERDICT: up, serving, and nothing wrong found.",
             WRONG: "VERDICT: something is wrong above. Nothing was restarted; see docs/BOT_STATUS_RUNBOOK.md.",
             UNKNOWN: "VERDICT: some checks could not run; read the report as incomplete, not as healthy."}
            [rep.worst])
    return rep


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only bot status: supervisor, processes, ports, logs.")
    ap.add_argument("--root", type=Path, default=ROOT, help="the checkout the bot runs from")
    ap.add_argument("--lines", type=int, default=40, help=f"log lines per source (at most {MAX_LINES})")
    ap.add_argument("--log", type=Path, action="append", default=[], help="an extra log file to tail")
    args = ap.parse_args(argv)
    rep = build(args.root, lines=args.lines, extra=args.log)
    print("\n".join(rep.lines))
    return rep.worst


if __name__ == "__main__":
    sys.exit(main())
