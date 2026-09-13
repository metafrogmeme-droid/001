"""Every door `docs/INCOME_MAP.md` prints has to still be a door.

A capability document is a card that names commands, at the scale of a whole
product — and this repo's most repeated lesson is that a card naming a command
is claiming the command does something. `/vault` printed fix hints for four
keys no invocation writes; the web capability card offered six rows that
reached no rule and no tool; `/help` named 91 commands of which 79 landed on a
tool-less model. Each was true when written. None had anything that would
notice when it stopped being true.

So the doc prints doors in a form that resolves, and this resolves them — by
READING THE TABLES THAT DISPATCH, not by grepping for the literal:

  /scan                  the Telegram command catalogue
  /letter                `app.get('/letter/:week?')` in app/server.js
  /api/copy/picks        the Express mount table, then the router it mounts
  /positions             the bot gateway's aiohttp route table
  /alerts                the web chat intercept table

A scan cannot see a map that is computed, and computing it is the whole reason
these tables exist; so each is read from the file that dispatches off it.

WHAT THIS DOES NOT CHECK, stated rather than implied. It checks that a door
EXISTS, not that the door leads where the row says, and not that the prose
beside it is true. `words_reach` records that distinction in as many words: a
rule or a tool CLAIMING a row is not the row's skill answering. The doc says
the same thing about itself in its own header — everything but the door names
is unverified code-reading — and that sentence being present is itself pinned
below, because the coverage note is the part a later edit would quietly drop.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DOC = REPO / "docs" / "INCOME_MAP.md"
SERVER = REPO / "app" / "server.js"
GATEWAY = REPO / "bot" / "web" / "user_gateway.py"
CHAT = REPO / "app" / "routes" / "chat.js"


# ── the five registries that actually dispatch ──────────────────────────────

def slash_commands() -> set[str]:
    from bot.skills.command_catalog import all_entries
    return {"/" + name for name in all_entries()}


def express_mounts() -> set[str]:
    return set(re.findall(r"app\.use\(\s*'(/[^']*)'", SERVER.read_text()))


def express_pages() -> set[str]:
    return set(re.findall(r"app\.get\(\s*'(/[^']*)'", SERVER.read_text()))


def router_paths(mount: str) -> set[str]:
    """Sub-paths the router(s) mounted at `mount` declare.

    Three spellings reach a router file and a first draft of this read one:

        app.use('/api/macro',  require('./routes/macro'))    inline
        app.use('/api/market', marketRouter)                 a plain variable
        app.use('/api/auth',   authRouter)                   destructured, and
                                                             from ./auth rather
                                                             than ./routes/auth

    and `/api/web3` is mounted TWICE — web3_execute first, then web3 — so
    taking the first match answered "no such route" for every path the second
    file declares. All mounts at the prefix are unioned.
    """
    src = SERVER.read_text()
    out: set[str] = set()
    for m in re.finditer(r"app\.use\(\s*%s\s*,\s*([^)]*)" % re.escape(repr(mount).replace('"', "'")), src):
        ref = m.group(1)
        hit = re.search(r"require\('\./([A-Za-z0-9_/]+)'", ref)
        if not hit:
            ident = re.match(r"\s*([A-Za-z0-9_]+)", ref)
            if not ident:
                continue
            name = re.escape(ident.group(1))
            hit = re.search(
                r"(?:const|let|var)\s+(?:%s|\{[^}]*\b%s\b[^}]*\})\s*=\s*require\('\./([A-Za-z0-9_/]+)'"
                % (name, name), src)
        if not hit:
            continue
        f = REPO / "app" / f"{hit.group(1)}.js"
        if not f.exists():
            continue
        out |= {
            mount.rstrip("/") + (p if p != "/" else "")
            for p in re.findall(r"router\.(?:get|post|put|delete)\(\s*'([^']*)'", f.read_text())
        }
    return out


def gateway_routes() -> set[str]:
    return set(re.findall(
        r"app\.router\.add_(?:get|post|put|delete)\(\s*\"([^\"]+)\"", GATEWAY.read_text()))


def chat_intercepts() -> set[str]:
    block = re.search(r"const INTERCEPTS = \[(.*?)\n\];", CHAT.read_text(), re.S)
    if not block:
        return set()
    return set(re.findall(r"^\s*\['([a-z0-9_]+)',", block.group(1), re.M))


def resolve(door: str) -> str | None:
    """Which registry claims this door, or None. Returns the registry NAME so a
    failure can say where it looked."""
    door = door.rstrip(".,;/*")
    if door in slash_commands():
        return "telegram command"
    for page in express_pages():
        # `/letter` is served by `app.get('/letter/:week?')`.
        if door == page or door == (page.split("/:")[0].rstrip("/") or "/"):
            return "web page"
    mounts = express_mounts()
    for mount in sorted(mounts, key=len, reverse=True):
        if door == mount:
            return "express mount"
        if door.startswith(mount.rstrip("/") + "/"):
            # The MOUNT existing is not the SUB-PATH existing. Accepting any
            # path under a real mount was the first draft, and it is a
            # fail-open: every invented one would have resolved.
            return "express route" if door in router_paths(mount) else None
    if door in gateway_routes():
        return "bot gateway route"
    if door.lstrip("/") in chat_intercepts():
        return "web chat intercept"
    return None


def doors_in_doc() -> list[str]:
    """Every door the document prints, read out of its own tables.

    Doors are the backticked cells of the third column. Nothing else in the
    file is read as a door, so prose may mention whatever it likes.
    """
    doors: list[str] = []
    for line in DOC.read_text().splitlines():
        if not line.startswith("|") or line.count("|") < 4:
            continue
        cell = line.split("|")[3]
        doors += re.findall(r"`(/[^`]+)`", cell)
    return doors


# ── the guard ───────────────────────────────────────────────────────────────

def test_the_document_prints_some_doors():
    """A parser that quietly matches nothing turns this whole file into a pass.

    That is the shape `ruff_gate.check_version` separates out and the shape the
    reachability sweep's own blind spots kept taking: a checker with nothing to
    check reports success.
    """
    doors = doors_in_doc()
    assert len(doors) > 100, f"only {len(doors)} doors parsed out of the map — the reader broke"


@pytest.mark.parametrize("door", sorted(set(doors_in_doc())))
def test_every_door_the_map_prints_resolves(door):
    where = resolve(door)
    assert where, (
        f"docs/INCOME_MAP.md offers `{door}` as a door and nothing dispatches it.\n"
        f"Checked: the Telegram command catalogue, app/server.js pages and mounts "
        f"(and each mounted router's own paths), the bot gateway route table, and "
        f"the web chat intercept list.\n"
        f"Either the door was renamed — fix the map — or it was removed, and the "
        f"map is now promising a capability nobody can reach."
    )


def test_a_door_that_does_not_exist_does_not_resolve():
    """The guard's other direction. A resolver that says yes to everything
    passes the test above against a map full of invented commands."""
    for invented in ("/notacommand", "/api/copy/nonsense", "/api/market/notreal",
                     "/api/auth/notreal", "/definitelynotapage"):
        assert resolve(invented) is None, f"{invented} resolved, so the resolver fails open"


def test_each_registry_is_read_and_not_empty():
    """An empty registry acquits every door it would have refused — the quiet
    direction. Each is read from a live file, so a rename that empties one has
    to fail here rather than silently widen what the map may claim."""
    for name, got in (
        ("telegram commands", slash_commands()),
        ("express mounts", express_mounts()),
        ("express pages", express_pages()),
        ("gateway routes", gateway_routes()),
        ("chat intercepts", chat_intercepts()),
    ):
        assert len(got) > 5, f"{name} read as {len(got)} entries — the reader is broken, not the tree"


def test_a_mounted_routers_own_paths_are_read_through_all_three_bindings():
    """Driven rather than asserted in prose: the inline require, the plain
    variable, and the destructured one each have to yield real sub-paths."""
    assert resolve("/api/macro") in ("express mount", "express route")      # inline require
    assert resolve("/api/market/dex") == "express route"                    # marketRouter
    assert resolve("/api/auth/referrals") == "express route"                # { router: authRouter }
    assert resolve("/api/web3/profile") == "express route"                  # the SECOND mount


def test_the_map_still_says_what_it_does_not_check():
    """The coverage note is the part an edit drops first, and a capability doc
    without it reads as a verified inventory. Two sentences are pinned: that
    the doors are the only checked claim, and that the rest is code-reading."""
    text = DOC.read_text()
    assert "That is the only claim in this file anything\nchecks." in text or \
           "the only claim in this file anything" in text, \
        "the map no longer says that its doors are the only checked claim"
    assert "It is code-reading, not execution." in text, \
        "the map no longer says its prose was never driven"
    assert "were not verified" in text, \
        "the map no longer says its file:line citations are unverified"
