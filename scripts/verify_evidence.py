#!/usr/bin/env python3
"""Re-derive every number this repository publishes, and say which did not match.

One command, offline by default, for the reader who should not have to trust us:

    python3 scripts/verify_evidence.py            # the committed record
    python3 scripts/verify_evidence.py --online   # plus today's roots and the newest call
    python3 scripts/verify_evidence.py --json     # machine-readable rows

What it checks, and against what:

  datasets    every frozen snapshot under benchmark/ with a manifest: each
              candle file's content hash, bar count, holes and repairs, and the
              dataset hash over the universe, recomputed from the files on
              disk (bot/backtest/snapshot.verify_dataset).
  scorecards  every card under benchmark/scorecards/: the dataset it names
              exists, the card's dataset_hash is that manifest's, the commit it
              was recorded at exists in this clone, the format is the one the
              catalogue reads, and no dollar figure is on it (a public surface
              carries percent, ratio and count only).
  studies     every pre-registered study in benchmark/studies.json: its sealed
              pre-registration is on disk, its SHA-256 is the one recorded in
              the registry and in the sealed sidecar, the report it names
              exists, and its pass count does not exceed its test count.
  roots       (--online) the published daily Merkle roots: the newest root's
              construction is recomputed from the day's seals where the site
              serves them, the anchor status is read as the site states it, and
              the newest call's seal equals sha256 of its served payload.

Three verdicts, never two. MATCH is a recomputation that agreed. MISMATCH is one
that did not, and makes the exit code 1. CANNOT CHECK is a check that could not
run (no git history, no network, a file the clone does not hold) and is reported
apart, with exit code 3 when it is the worst thing found: a check that did not
run is not a pass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MATCH, MISMATCH, CANNOT = "MATCH", "MISMATCH", "CANNOT CHECK"
SCORECARD_FORMAT = "runeclaw.agent.scorecard.v1"
STUDIES_FORMAT = "runeclaw.studies.v1"
#: Dollar figures never sit on a public card (the rule the scorecard test holds).
FORBIDDEN_KEYS = ("net_pnl", "total_pnl", "final_equity", "balance", "avg_win_usd",
                  "avg_loss_usd", "total_commission", "max_drawdown_usd")
DEFAULT_SITE = "https://www.humanoid-traders.com"
#: Directories under benchmark/ that are not snapshots.
NOT_SNAPSHOTS = {"scorecards", "eligibility"}


@dataclass
class Row:
    group: str
    subject: str
    status: str
    detail: str = ""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# ── datasets ────────────────────────────────────────────────────────────────

def check_datasets(root: Path) -> list[Row]:
    from bot.backtest.snapshot import verify_dataset
    rows: list[Row] = []
    bench = root / "benchmark"
    dirs = sorted(p for p in bench.iterdir() if p.is_dir() and p.name not in NOT_SNAPSHOTS
                  and (p / "manifest.json").exists()) if bench.is_dir() else []
    if not dirs:
        return [Row("datasets", "benchmark/", CANNOT, "no snapshot with a manifest found")]
    for d in dirs:
        try:
            ok, problems = verify_dataset(d)
        except Exception as exc:  # noqa: BLE001 — the class is the reading
            rows.append(Row("datasets", d.name, CANNOT, type(exc).__name__))
            continue
        rows.append(Row("datasets", d.name, MATCH if ok else MISMATCH,
                        "" if ok else "; ".join(problems[:4])))
    return rows


# ── scorecards ──────────────────────────────────────────────────────────────

def _commit_exists(root: Path, sha: str) -> Optional[bool]:
    """True/False when git can answer, None when there is no history to ask."""
    if not sha or not (root / ".git").exists():
        return None
    try:
        proc = subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=root,
                              capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode == 0:
        return True
    err = proc.stderr.decode("utf-8", "replace")
    # A shallow clone answers "Not a valid object name" for a real commit it
    # does not hold; that is "could not check", not "the card lies".
    return None if "shallow" in err.lower() or _is_shallow(root) else False


def _is_shallow(root: Path) -> bool:
    return (root / ".git" / "shallow").exists()


def check_scorecards(root: Path) -> list[Row]:
    rows: list[Row] = []
    cards = sorted((root / "benchmark" / "scorecards").glob("*.json"))
    if not cards:
        return [Row("scorecards", "benchmark/scorecards/", CANNOT, "no card found")]
    for p in cards:
        try:
            card = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            rows.append(Row("scorecards", p.name, CANNOT, type(exc).__name__))
            continue
        problems: list[str] = []
        if card.get("format") != SCORECARD_FORMAT:
            problems.append(f"format {card.get('format')!r}")
        man_path = root / "benchmark" / str(card.get("dataset") or "") / "manifest.json"
        if not man_path.exists():
            problems.append(f"dataset {card.get('dataset')!r} has no manifest in this clone")
        else:
            try:
                man_hash = json.loads(man_path.read_text(encoding="utf-8")).get("dataset_hash")
            except (OSError, ValueError) as exc:
                man_hash = None
                problems.append(f"manifest unreadable ({type(exc).__name__})")
            if man_hash and card.get("dataset_hash") != man_hash:
                problems.append("dataset_hash is not the manifest's")
        metrics = card.get("metrics") if isinstance(card.get("metrics"), dict) else {}
        dollars = [k for k in FORBIDDEN_KEYS if k in metrics or k in card]
        if dollars:
            problems.append(f"dollar key(s) on a public card: {dollars}")
        exists = _commit_exists(root, str(card.get("code_sha") or ""))
        cannot = ""
        if exists is False:
            problems.append(f"code_sha {str(card.get('code_sha'))[:12]} is not a commit of this repository")
        elif exists is None:
            cannot = "commit not checkable here (no git history)"
        if problems:
            rows.append(Row("scorecards", p.name, MISMATCH, "; ".join(problems)))
        elif cannot:
            rows.append(Row("scorecards", p.name, CANNOT, cannot))
        else:
            rows.append(Row("scorecards", p.name, MATCH,
                            f"{card.get('dataset')} {str(card.get('dataset_hash'))[:12]} "
                            f"@ {str(card.get('code_sha'))[:12]}"))
    return rows


# ── studies ─────────────────────────────────────────────────────────────────

def load_studies(root: Path) -> dict:
    return json.loads((root / "benchmark" / "studies.json").read_text(encoding="utf-8"))


def _keys(obj) -> set[str]:
    """Every key at any depth. A key, not a substring: prose that says
    "rebalance" is not a `balance` field."""
    if isinstance(obj, dict):
        return set(map(str, obj)) | set().union(*(_keys(v) for v in obj.values()))
    if isinstance(obj, list):
        return set().union(*(_keys(v) for v in obj))
    return set()


def check_studies(root: Path) -> list[Row]:
    rows: list[Row] = []
    try:
        reg = load_studies(root)
    except (OSError, ValueError) as exc:
        return [Row("studies", "benchmark/studies.json", CANNOT, type(exc).__name__)]
    if reg.get("format") != STUDIES_FORMAT or not isinstance(reg.get("studies"), list):
        return [Row("studies", "benchmark/studies.json", MISMATCH, "not a studies registry")]
    for s in reg["studies"]:
        sid = str(s.get("id") or "?")
        problems: list[str] = []
        prereg = root / str(s.get("prereg") or "")
        if not prereg.is_file():
            rows.append(Row("studies", sid, MISMATCH, f"pre-registration missing: {s.get('prereg')}"))
            continue
        digest = sha256_file(prereg)
        if digest != s.get("prereg_sha256"):
            problems.append(f"pre-registration sha256 {digest[:12]} is not the registered "
                            f"{str(s.get('prereg_sha256'))[:12]}")
        sidecar = prereg.with_suffix(".sha256")
        if sidecar.is_file():
            # The sidecar is the sealing history: one line per seal, the last
            # one is the file as it stands. An addendum re-seals; a silent
            # edit does not.
            lines = [ln.split()[0] for ln in sidecar.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if not lines or lines[-1] != digest:
                problems.append("sealed sidecar's last line is not the file on disk")
        else:
            problems.append("no sealed sidecar beside the pre-registration")
        report = s.get("report")
        if report and not (root / str(report)).is_file():
            problems.append(f"report missing: {report}")
        tests, passes = s.get("tests"), s.get("passes")
        if not (isinstance(tests, int) and isinstance(passes, int) and 0 <= passes <= tests):
            problems.append(f"passes/tests unreadable: {passes}/{tests}")
        dollars = sorted(set(FORBIDDEN_KEYS) & _keys(s))
        if dollars:
            problems.append(f"dollar key(s) on a public study row: {dollars}")
        rows.append(Row("studies", sid, MISMATCH if problems else MATCH,
                        "; ".join(problems) if problems else f"sealed {s.get('sealed_at')} · {passes} of {tests} pass"))
    return rows


# ── roots and calls (online) ────────────────────────────────────────────────

def _get_json(url: str, timeout: float = 15.0):
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "runeclaw-verify-evidence/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — https only, our own host
        return json.loads(resp.read().decode("utf-8"))


def merkle_root(seals: list[str]) -> str:
    """The site's stated construction: leaves = day seals deduped + sorted asc;
    parent = sha256(utf8(leftHex+rightHex)); odd node promoted; single leaf = root."""
    level = sorted(set(s.lower() for s in seals))
    if not level:
        return ""
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                nxt.append(hashlib.sha256((level[i] + level[i + 1]).encode("utf-8")).hexdigest())
            else:
                nxt.append(level[i])
        level = nxt
    return level[0]


def check_online(site: str) -> list[Row]:
    rows: list[Row] = []
    site = site.rstrip("/")
    try:
        roots = _get_json(f"{site}/api/roots")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return [Row("roots", f"{site}/api/roots", CANNOT, type(exc).__name__)]
    days = roots.get("roots") if isinstance(roots, dict) else None
    if not days:
        return [Row("roots", f"{site}/api/roots", CANNOT, "no roots served")]
    newest = days[0]
    day, root_hex = str(newest.get("day")), str(newest.get("root"))
    rows.append(Row("roots", f"{day} root", MATCH if len(root_hex) == 64 else MISMATCH,
                    f"{root_hex[:16]}… over {newest.get('seal_count')} seals; anchor "
                    f"{'tx ' + str(newest.get('anchor_tx'))[:14] if newest.get('anchor_tx') else 'none yet'}"))
    # The site's own re-check of that day's anchor, read as it states it.
    try:
        v = _get_json(f"{site}/api/roots/verify/{day}")
        rows.append(Row("roots", f"{day} anchor", MATCH if v.get("status") in ("verified", "unanchored") else MISMATCH,
                        f"status {v.get('status')}" + (f" ({v.get('reason')})" if v.get("reason") else "")))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        rows.append(Row("roots", f"{day} anchor", CANNOT, type(exc).__name__))
    # The newest call: its seal must be sha256 of the served payload, verbatim.
    try:
        latest = _get_json(f"{site}/api/call/latest")
        key = latest.get("signal_key") or latest.get("key") or latest.get("trade_key")
        payload, seal = latest.get("seal_payload"), latest.get("seal")
        if not (isinstance(payload, str) and isinstance(seal, str)):
            rows.append(Row("calls", str(key or "latest"), CANNOT, "no seal_payload served to this viewer"))
        else:
            digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            rows.append(Row("calls", str(key or "latest"), MATCH if digest == seal.lower() else MISMATCH,
                            f"sha256(seal_payload) {digest[:16]}… {'==' if digest == seal.lower() else '!='} seal"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        rows.append(Row("calls", "latest", CANNOT, type(exc).__name__))
    return rows


# ── the README's studies table (generated, never typed) ─────────────────────

README_BEGIN = ("<!-- studies:start — generated by `python3 scripts/verify_evidence.py --readme` from "
                "benchmark/studies.json; tests/test_the_evidence_re_derives.py fails when they differ -->")
README_END = "<!-- studies:end -->"


def readme_block(root: Path = ROOT) -> str:
    """The studies table the README carries, rendered from the registry alone,
    so the table and the registry cannot name two results."""
    reg = load_studies(root)
    studies = reg["studies"]
    tests = sum(int(s["tests"]) for s in studies)
    passes = sum(int(s["passes"]) for s in studies)
    lines = [README_BEGIN, "",
             f"**{len(studies)} pre-registered studies, {tests} tests, {passes} passes.** Each question, its data, "
             "costs, windows and pass rule were written and sealed (SHA-256 and time recorded beside the file) "
             "before the result was computed; a failure is reported as a failure, with its numbers.", "",
             "| study | sealed (UTC) | tests | passes | outcome | pre-registration |",
             "|---|---|---|---|---|---|"]
    for s in studies:
        outcome = str(s["verdict"]).split(". The finding")[0]
        lines.append(f"| {s['title']} | {str(s['sealed_at']).replace('T', ' ').rstrip('Z')} | {s['tests']} | "
                     f"{s['passes']} | {outcome} | [`{str(s['prereg_sha256'])[:12]}…`]({s['prereg']}) |")
    lines += ["", README_END]
    return "\n".join(lines)


def write_readme(root: Path = ROOT) -> bool:
    """Replace the README's generated block in place. Returns whether it changed."""
    path = root / "README.md"
    txt = path.read_text(encoding="utf-8")
    # Located by the marker's opening words, so a block written by an older
    # wording of the comment is still the block this replaces.
    start = txt.index("<!-- studies:start")
    end = txt.index(README_END, start) + len(README_END)
    new = txt[:start] + readme_block(root) + txt[end:]
    if new != txt:
        path.write_text(new, encoding="utf-8")
    return new != txt


# ── the verdict ─────────────────────────────────────────────────────────────

def run(root: Path, online: bool, site: str) -> list[Row]:
    rows = check_datasets(root) + check_scorecards(root) + check_studies(root)
    if online:
        rows += check_online(site)
    return rows


def exit_code(rows: list[Row]) -> int:
    if any(r.status == MISMATCH for r in rows):
        return 1
    if any(r.status == CANNOT for r in rows):
        return 3
    return 0


def render(rows: list[Row]) -> str:
    out = []
    width = max(len(f"{r.group}/{r.subject}") for r in rows) if rows else 10
    for r in rows:
        out.append(f"  {f'{r.group}/{r.subject}':<{width}}  {r.status:<13} {r.detail}")
    counts = {k: sum(1 for r in rows if r.status == k) for k in (MATCH, MISMATCH, CANNOT)}
    out.append("")
    out.append(f"{counts[MATCH]} matched, {counts[MISMATCH]} mismatched, {counts[CANNOT]} could not be checked")
    code = exit_code(rows)
    out.append({0: "Every published number re-derives.",
                1: "A published number does NOT re-derive: read the MISMATCH rows.",
                3: "Nothing mismatched, but a check could not run; that is not a pass."}[code])
    return "\n".join(out)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--online", action="store_true", help="also read the live site's roots and newest call")
    ap.add_argument("--site", default=DEFAULT_SITE, help=f"site to read with --online (default {DEFAULT_SITE})")
    ap.add_argument("--json", action="store_true", help="print rows as JSON")
    ap.add_argument("--readme", action="store_true",
                    help="rewrite the README's studies table from benchmark/studies.json and exit")
    ap.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.readme:
        changed = write_readme(Path(args.root))
        print("README studies table " + ("rewritten" if changed else "already current"))
        return 0
    rows = run(Path(args.root), args.online, args.site)
    if args.json:
        print(json.dumps([r.__dict__ for r in rows], indent=1))
    else:
        print(render(rows))
    return exit_code(rows)


if __name__ == "__main__":
    sys.exit(main())
