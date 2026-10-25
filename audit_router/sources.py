"""Solidity sources the router is willing to read.

``script/``, ``test/`` and build output are not the contract under review.
Comments and strings are masked in place (newlines kept) so a routing
pattern in a comment is not a pattern in the code, and a match index is
still the line in the original file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_SKIP = {"test", "tests", "script", "scripts", "out", "lib", "node_modules",
         "cache", "artifacts", "broadcast"}
_DECL = re.compile(
    r"^[ \t]*(?:abstract[ \t]+)?(?:contract|library|interface)[ \t]+\w+",
    re.M,
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_inside_repo(path: Path) -> Path:
    """Absolute path under the repo. A path that escapes is refused."""
    root = repo_root()
    target = path if path.is_absolute() else (Path.cwd() / path)
    target = target.resolve()
    target.relative_to(root)
    return target


@dataclass(frozen=True)
class SourceFile:
    rel: str
    text: str
    readable: bool
    error: str = ""


@dataclass(frozen=True)
class Chunk:
    rel: str
    text: str
    start_line: int
    end_line: int


def mask_sol(src: str) -> str:
    """Comments and string contents become spaces. Newlines stay."""
    out: list[str] = []
    i = 0
    n = len(src)
    while i < n:
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join("\n" if c == "\n" else " " for c in src[i:j]))
            i = j
            continue
        if src[i] in "\"'":
            quote = src[i]
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == quote:
                    j += 1
                    break
                if src[j] == "\n" and quote != "`":
                    break
                j += 1
            out.append("".join("\n" if c == "\n" else " " for c in src[i:j]))
            i = j
            continue
        out.append(src[i])
        i += 1
    return "".join(out)


def _read_sol(path: Path, root: Path) -> SourceFile:
    rel = path.relative_to(root).as_posix()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return SourceFile(rel, "", False, type(exc).__name__)
    return SourceFile(rel, text, True)


def project_dir(target: Path) -> Path:
    """Nearest directory at or above ``target`` that names a chain or Foundry.

    A file under ``src/`` still sees ``networks.json`` in the project root.
    Walking stops at the repository root.
    """
    root = repo_root()
    cur = target if target.is_dir() else target.parent
    fallback = cur
    while True:
        if any((cur / name).is_file()
               for name in ("networks.json", "chains.json", "foundry.toml")):
            return cur
        if cur == root or cur.parent == cur:
            return fallback
        cur = cur.parent


def collect_sources(target: Path) -> tuple[list[SourceFile], Path | None]:
    """Solidity under ``src/`` when that directory exists, else the folder.

    The second value is the project directory whose chain config is read.
    """
    root = repo_root()
    target = resolve_inside_repo(target)
    if target.is_file():
        if target.suffix != ".sol":
            return [], project_dir(target)
        return [_read_sol(target, root)], project_dir(target)
    if not target.is_dir():
        return [], None
    base = target / "src" if (target / "src").is_dir() else target
    files: list[SourceFile] = []
    for path in sorted(base.rglob("*.sol")):
        parts = path.relative_to(base).parts
        if any(part in _SKIP for part in parts):
            continue
        files.append(_read_sol(path, root))
    return files, project_dir(target)


def _line_of(src: str, idx: int) -> int:
    return src.count("\n", 0, idx) + 1


def _spans(source: str) -> list[tuple[int, int]]:
    masked = mask_sol(source)
    matches = list(_DECL.finditer(masked))
    if len(matches) <= 1:
        return [(0, len(source))]
    spans: list[tuple[int, int]] = []
    if matches[0].start() > 0:
        spans.append((0, matches[0].start()))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(source)
        spans.append((match.start(), end))
    return spans


def chunk_source(src: SourceFile, chunk_tokens: int, estimate) -> list[Chunk] | None:
    """Split one file by contract, then by line, so each chunk fits.

    None means the token estimate could not be read. A single line that
    is itself over the budget is still returned: dropping it would hide
    the source. ``estimate`` is the conversation store's estimator.
    """
    if not src.readable:
        return None
    text = src.text if src.text.endswith("\n") or src.text == "" else src.text + "\n"

    def fits(body: str) -> bool | None:
        cost = estimate(body)
        if cost is None:
            return None
        return cost <= chunk_tokens

    def emit(body: str, start_idx: int) -> Chunk:
        start = _line_of(text, start_idx)
        # The slice's own trailing newline belongs to its last line.
        length = len(body)
        end_idx = start_idx + max(0, length - 1)
        end = _line_of(text, end_idx)
        return Chunk(src.rel, body, start, end)

    whole = fits(text)
    if whole is None:
        return None
    if whole:
        return [emit(text, 0)]

    chunks: list[Chunk] = []
    for start, end in _spans(text):
        body = text[start:end]
        decision = fits(body)
        if decision is None:
            return None
        if decision:
            chunks.append(emit(body, start))
            continue
        lines = body.splitlines(keepends=True)
        buf: list[str] = []
        buf_at = start
        used = 0
        cursor = start
        for line in lines:
            cost = estimate(line)
            if cost is None:
                return None
            if buf and used + cost > chunk_tokens:
                chunks.append(emit("".join(buf), buf_at))
                buf = []
                used = 0
                buf_at = cursor
            buf.append(line)
            used += cost
            cursor += len(line)
        if buf:
            chunks.append(emit("".join(buf), buf_at))
    return chunks


def chunk_files(files: list[SourceFile], chunk_tokens: int, estimate) -> list[Chunk] | None:
    chunks: list[Chunk] = []
    for src in files:
        part = chunk_source(src, chunk_tokens, estimate)
        if part is None:
            return None
        chunks.extend(part)
    return chunks
