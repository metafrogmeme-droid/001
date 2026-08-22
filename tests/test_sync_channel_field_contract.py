"""
Every field the bot reads off a synced row is a field the website emits.

The Express app and the Python bot are separate processes in different
languages joined by `/api/bot/sync/*`. On each `…/pending` endpoint the JS side
names the payload's fields in a SQL `SELECT` column list and hands the rows
straight out as JSON; the Python side reads them back by string key. Nothing
spans that seam: the JS suite asserts the route returns rows, the Python suite
feeds its consumers hand-written dicts, and both are green whichever names each
one happens to use.

A rename on either side is therefore silent, and silent in the worst direction
this repo has a rule about — `r.get("telegram_id")` on a row that no longer has
that column is `None`, which every consumer here reads as *this row is not for
a user*, and every one of them then `continue`s. The row is never acked, so the
website never clears it, so it is pulled again forever. No exception, no log
line, no failing test, and on `/flatten/pending` the row being dropped is a
user pressing EMERGENCY STOP.

MEASURED FIRST, and the measurement was a clean negative: all four contracts
agree today. This exists because nothing was holding them there. It is the
same finding as `tests/test_creds_envelope_cross_runtime.py` one level up —
that one is about the *bytes* crossing this boundary, this one about the
*names* — and both come from the sweep that found `app/lib/canonical.js`.

WHY A SOURCE SCAN. CLAUDE.md reserves these for "shapes a unit test cannot
reach", and a contract between two runtimes is the clearest case there is: no
process in either test suite can observe both ends. Booting Express and driving
it from pytest would be the behavioural version, and it would pin the same two
lists at several times the cost and fragility. The scan is bounded by CODE at
both ends on both sides, and it proves its own extractors below before
trusting them — because an extractor that quietly finds nothing reports perfect
agreement.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

import bot.utils.control_pull as control_pull
import bot.utils.credential_pull as credential_pull

REPO = Path(__file__).resolve().parents[1]
SYNC_JS = REPO / "app" / "routes" / "sync.js"
ENGINE_PY = REPO / "bot" / "core" / "engine.py"


def _strip_js_comments(src: str) -> str:
    """Drop `//` and `/* */`, keeping string literals intact.

    The repo's standing rule, earned six times: a comment quoting the thing it
    describes is indistinguishable from the code doing it. The route docstrings
    here list field names in prose (`Body: { acks: [{ user_id, action, ok }] }`),
    so a scan that kept them would find columns the SQL never selects.
    """
    out = []
    i, n = 0, len(src)
    quote = None
    while i < n:
        c = src[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"`":
            quote = c
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
            continue
        out.append(c)
        i += 1
    return "".join(out)


def js_selected_columns(route: str) -> set[str]:
    """The column names `router.get('<route>')` puts into its JSON rows.

    Bounded by code: the handler's own `router.get` line to the first `SELECT`
    inside it. `AS` aliases are honoured — the alias is what reaches the wire.
    """
    src = _strip_js_comments(SYNC_JS.read_text())
    m = re.search(r"router\.get\(\s*['\"]" + re.escape(route) + r"['\"]", src)
    assert m, f"{route} is gone from app/routes/sync.js"
    sel = re.search(r"SELECT\s+(.+?)\s+FROM\s", src[m.end():], re.S | re.I)
    assert sel, f"no SELECT found in the handler for {route}"
    cols = set()
    for part in sel.group(1).split(","):
        part = part.strip()
        alias = re.search(r"\bAS\s+`?(\w+)`?$", part, re.I)
        cols.add((alias.group(1) if alias else part.split(".")[-1]).strip("` "))
    return cols


def _code_only(src: str) -> str:
    """Python source with its docstrings removed.

    Docstrings are stripped for SCANNING only, and only here. This repo does
    not strip them wholesale — a docstring is a value, readable as `__doc__`
    and by `inspect`, so removing it can change behaviour — but these
    particular ones describe the row shape in prose, and prose that names a
    field is exactly what must not be mistaken for code reading it.
    """
    try:
        tree = ast.parse(inspect.cleandoc(src) if src.startswith(" ") else src)
    except SyntaxError:  # a bounded block need not parse on its own
        return re.sub(r'"""[\s\S]*?"""', "", src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Module)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                src = src.replace(doc, "", 1)
    return src


ROW_GET = re.compile(r"\b(?:r|row)\.get\(\s*['\"](\w+)['\"]")


def py_row_keys(source: str) -> set[str]:
    """Keys read off a synced row (`r.get("x")` / `row.get("x")`), code only.

    Deliberately narrow to `r`/`row`: `credential_pull` also does
    `creds.get("venue")`, and `creds` is the DECRYPTED PAYLOAD, not the row —
    a field the website never sends as a column and must not be demanded of it.
    """
    return set(ROW_GET.findall(_code_only(source)))


def engine_flatten_block() -> str:
    """The engine's inline flatten consumer, bounded by code at both ends."""
    src = ENGINE_PY.read_text()
    i = src.index("from bot.utils.control_pull import fetch_flatten_pending")
    j = src.index("ack_flatten, acks", i)
    return src[i:j]


# ── The four contracts ─────────────────────────────────────────────────────
# (route, human name, python-side source). Kept as a table so a fifth pulled
# endpoint is one line, and so a route that loses its consumer is visible.
CONTRACTS = [
    ("/credentials/pending", "exchange API keys",
     lambda: inspect.getsource(credential_pull.process_pending)),
    ("/controls/pending", "live on/off, margin cap, pause",
     lambda: inspect.getsource(control_pull.process_pending_controls)),
    ("/stance/pending", "operator stance",
     lambda: inspect.getsource(control_pull.pull_and_apply_stance)),
    ("/flatten/pending", "emergency stop",
     engine_flatten_block),
]


class TestExtractorsWork:
    """Prove both halves see known values before any verdict is believed.

    A checker with a blind spot manufactures exactly the accusation it exists
    to prevent — and here the blind spot points the other way: an extractor
    that finds nothing reports every contract as satisfied.
    """

    def test_js_extractor_finds_known_columns(self):
        cols = js_selected_columns("/credentials/pending")
        assert {"user_id", "telegram_id", "encrypted_payload"} <= cols, cols

    def test_js_extractor_ignores_the_docstring_above_the_route(self):
        # The `/credentials/ack` doc block lists `{ user_id, action, ok }`;
        # `ok` is never a selected column and must not be picked up as one.
        assert "ok" not in js_selected_columns("/credentials/pending")

    def test_python_extractor_finds_known_keys(self):
        keys = py_row_keys(inspect.getsource(credential_pull.process_pending))
        assert {"user_id", "telegram_id", "action", "encrypted_payload"} <= keys, keys

    def test_python_extractor_ignores_the_decrypted_payload(self):
        # `creds.get("venue")` reads the decrypted blob, not the row. Demanding
        # a `venue` column would be a false failure against correct code.
        assert "venue" not in py_row_keys(
            inspect.getsource(credential_pull.process_pending))

    def test_the_comparison_would_actually_fail(self):
        # Anti-vacuity: everything above is satisfied by two empty sets.
        cols = js_selected_columns("/credentials/pending")
        assert not {"telegram_id", "a_column_nobody_selects"} <= cols


class TestContracts:
    @pytest.mark.parametrize("route,what,get_src",
                             CONTRACTS, ids=[c[0] for c in CONTRACTS])
    def test_every_key_the_bot_reads_is_a_column_the_site_sends(self, route, what, get_src):
        cols = js_selected_columns(route)
        keys = py_row_keys(get_src())
        assert keys, (
            f"{route}: no row key found on the Python side — the consumer moved "
            "or was rewritten, and this contract is now unchecked rather than "
            "satisfied"
        )
        missing = keys - cols
        assert not missing, (
            f"{route} ({what}): the bot reads {sorted(missing)} off each row and "
            f"app/routes/sync.js does not select {'it' if len(missing) == 1 else 'them'}. "
            "Every such field arrives as None. Where it is the id, the row is "
            "skipped and never acked, so the website re-serves it forever with "
            "nothing raised and nothing logged."
        )
