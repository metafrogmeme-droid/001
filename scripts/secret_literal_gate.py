#!/usr/bin/env python3
"""Fail if the current tree contains a contiguous provider-pattern secret.

This is the TREE gate, not a history scan. Historical values stay in git
and are a rotation problem. This script answers: "is a Telegram bot token,
Google API key, LLM provider key, JWT, or PEM private key written as one
literal in the files we have now?"

Allowed files are only the runtime fake builders and the official secret
vocabulary table (plus its generated website artifact). Every other path
is scanned.

Findings are reported as rule, path and line. The matching text is never
printed.

  python3 scripts/secret_literal_gate.py          # gate
  python3 scripts/secret_literal_gate.py --paths tests/foo.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Official table + runtime builders. A real key pasted into these files
# tomorrow is still a problem — the builders must keep assembling parts,
# and the table is the one documented exception.
ALLOWED = {
    "tests/fake_secrets.py",
    "app/test/fake_secrets.js",
    "bot/utils/secret_shapes.py",
    "app/lib/secret_shapes.generated.json",
    # Scanner config holds documented stopwords for the history scan; a
    # value-regex allowlist is load-bearing there and is not a runtime secret.
    ".gitleaks.toml",
}

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    "target", ".ruff_cache", ".mypy_cache", ".pytest_cache",
}
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".woff", ".woff2", ".pdf",
    ".bin", ".so", ".wasm", ".map", ".lock",
}
SCAN_SUFFIXES = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".md", ".json",
    ".yml", ".yaml", ".toml", ".txt", ".sh", ".env", ".example",
}

# Lengths follow bot/utils/secret_shapes.py (the one vocabulary) and the
# shapes GitHub's provider patterns flag.
RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("telegram_bot_token", re.compile(r"(?:bot)?\d{6,12}:[A-Za-z0-9_-]{20,}")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}")),
    ("openai_key", re.compile(r"\bsk-(?:proj-|svcacct-|live-)?[A-Za-z0-9_-]{20,}")),
    ("xai_key", re.compile(r"\bxai-[A-Za-z0-9_-]{20,}")),
    ("groq_key", re.compile(r"\bgsk_[A-Za-z0-9_-]{20,}")),
    ("perplexity_key", re.compile(r"\bpplx-[A-Za-z0-9_-]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("pem_private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
)

# openai_key also matches sk-ant-...; drop those, they already have a rule.
_ANTHROPIC = re.compile(r"^sk-ant-")


def iter_files(root: Path, only: list[Path] | None) -> list[Path]:
    if only:
        out = []
        for p in only:
            p = p if p.is_absolute() else (root / p)
            if p.exists() and p.is_file():
                out.append(p)
        return out
    files: list[Path] = []
    for dirpath, dirnames, filenames in root.walk() if hasattr(Path, "walk") else _os_walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            p = dirpath / fn
            if p.suffix.lower() in SKIP_SUFFIXES:
                continue
            if p.suffix.lower() not in SCAN_SUFFIXES and p.name not in {".env", ".env.example"}:
                continue
            if p.name in {"package-lock.json", "Cargo.lock"}:
                continue
            files.append(p)
    return files


def _os_walk(root: Path):
    import os
    for dirpath, dirnames, filenames in os.walk(root):
        yield Path(dirpath), dirnames, filenames


def relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def scan_text(text: str) -> list[tuple[str, int]]:
    """Return (rule, line_number) for each finding. Never includes the text."""
    found: list[tuple[str, int]] = []
    seen: set[tuple[str, int, int]] = set()
    for rule, pattern in RULES:
        for m in pattern.finditer(text):
            if rule == "openai_key" and _ANTHROPIC.match(m.group(0)):
                continue
            line = text.count("\n", 0, m.start()) + 1
            key = (rule, line, m.start())
            if key in seen:
                continue
            seen.add(key)
            found.append((rule, line))
    return found


def scan_tree(root: Path = ROOT, only: list[Path] | None = None) -> list[tuple[str, str, int]]:
    findings: list[tuple[str, str, int]] = []
    for path in iter_files(root, only):
        rel = relative(path)
        if rel in ALLOWED:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for rule, line in scan_text(text):
            findings.append((rule, rel, line))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--paths", nargs="*", default=None,
        help="Scan only these paths (for hooks). Default: the whole tree.",
    )
    args = parser.parse_args(argv)
    only = [Path(p) for p in args.paths] if args.paths else None
    findings = scan_tree(only=only)
    if not findings:
        print("secret_literal_gate: no contiguous provider-pattern literals")
        return 0
    print(
        "secret_literal_gate: contiguous provider-pattern literal(s) found. "
        "Build test fixtures at runtime with tests/fake_secrets.py or "
        "app/test/fake_secrets.js. Matching text is not printed.",
        file=sys.stderr,
    )
    for rule, rel, line in findings:
        print(f"  {rel}:{line}  rule={rule}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
