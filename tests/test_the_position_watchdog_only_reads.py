"""The position watchdog cannot place, amend or cancel an order.

Three things hold it, and each is measured here.

1. THE TRANSPORT. The watchdog's client is ccxt's, and a ccxt client has order
   methods. `read_only` wraps the one function every REST call leaves through,
   and refuses whatever is not a GET before it is sent. Driven: each write
   method is called on a guarded client and raises `WriteRefused`, and the
   transport underneath never sees it.

2. THE IMPORT CLOSURE. No module the watchdog imports, at any depth, lazy
   imports included, contains a call that places, amends or cancels an order
   or changes an account setting on a venue. The executor, the engine and the
   signed REST client are not in it. The walk is controlled both ways: it
   finds those calls in the executor, and it catches a planted import.

3. THE MODULE ITSELF. The only things it asks of its client are three reads
   and a close.

"A write" here is a venue order or account write: ccxt's order, cancel, edit,
leverage, margin-mode, position-mode, transfer and withdraw methods, a private
POST endpoint, and the signed client's POST. An HTTP POST to something that is
not a venue order route (the Telegram alert, a JSON-RPC read) is not one.
"""
from __future__ import annotations

import ast
import asyncio
import configparser
import re
from pathlib import Path

import pytest

import bot.position_watchdog as w
from tests.test_the_position_watchdog import SYMBOL, Venue, position

ROOT = Path(__file__).resolve().parents[1]
WATCHDOG = "bot.position_watchdog"

#: A call by one of these names is a venue order or account write.
WRITE_CALL = re.compile(
    r"^(create_\w*orders?\w*|cancel_\w*orders?\w*|edit_\w*orders?\w*|close_position|"
    r"close_all_positions|set_leverage|set_margin_mode|set_position_mode|transfer|withdraw|"
    r"private\w*Post\w*|_v3_post|v3_post)$")

#: Modules that hold the bot's order path, named so the failure is readable.
ORDER_PATH = ("bot.core.live_executor", "bot.core.engine", "bot.core.bitget_v3_client",
              "bot.core.venue_router", "bot.core.exchange_sync")


# --- the walk ----------------------------------------------------------------

def _path_of(module: str, root: Path):
    as_file = root / (module.replace(".", "/") + ".py")
    if as_file.is_file():
        return as_file
    as_package = root / module.replace(".", "/") / "__init__.py"
    return as_package if as_package.is_file() else None


def _imported(module: str, path: Path) -> set[str]:
    """Every module name an import statement in ``path`` could bind, wherever
    in the file the statement sits."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                up = package[: len(package) - (node.level - 1)]
                base = ".".join(up + ([base] if base else []))
            out.add(base)
            out.update(f"{base}.{alias.name}" for alias in node.names)
    return out


def closure(start: str, root: Path = ROOT) -> dict[str, Path]:
    """The repository modules reachable from ``start`` by import."""
    seen: dict[str, Path] = {}
    todo = [start]
    while todo:
        module = todo.pop()
        if module in seen:
            continue
        path = _path_of(module, root)
        if path is None:
            continue
        seen[module] = path
        todo.extend(_imported(module, path))
        if "." in module:
            todo.append(module.rsplit(".", 1)[0])
    return seen


def write_calls(path: Path) -> list[tuple[str, int]]:
    """``(name, line)`` of every call in ``path`` whose name is a venue write."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name and WRITE_CALL.match(name):
            hits.append((name, node.lineno))
    return hits


# --- 1. the transport --------------------------------------------------------

def _guarded(venue):
    return w.read_only(venue.client())


WRITES = [
    ("create_order", lambda ex: ex.create_order(SYMBOL, "market", "sell", 1.0)),
    ("create a closing trigger", lambda ex: ex.create_order(
        SYMBOL, "market", "sell", 1.0, None,
        {"triggerPrice": 9.0, "triggerType": "last", "productType": "USDT-FUTURES",
         "tradeSide": "close", "reduceOnly": True})),
    ("cancel_order", lambda ex: ex.cancel_order("1", SYMBOL, {"productType": "USDT-FUTURES"})),
    ("cancel a plan order", lambda ex: ex.cancel_order(
        "1", SYMBOL, {"productType": "USDT-FUTURES", "trigger": True, "planType": "normal_plan"})),
    ("cancel_all_orders", lambda ex: ex.cancel_all_orders(SYMBOL, {"productType": "USDT-FUTURES"})),
    ("edit_order", lambda ex: ex.edit_order("1", SYMBOL, "limit", "sell", 1.0, 11.0)),
    ("set_leverage", lambda ex: ex.set_leverage(5, SYMBOL)),
    ("set_margin_mode", lambda ex: ex.set_margin_mode("isolated", SYMBOL)),
    ("set_position_mode", lambda ex: ex.set_position_mode(True, SYMBOL)),
    ("close_position", lambda ex: ex.close_position(SYMBOL, "long")),
]


class TestOnlyAGetLeavesTheClient:
    @pytest.mark.parametrize("uta", [False, True], ids=["classic", "unified"])
    @pytest.mark.parametrize("name,call", WRITES, ids=[name for name, _ in WRITES])
    def test_a_write_is_refused_before_it_is_sent(self, name, call, uta):
        venue = Venue(positions=[position()], classic=not uta)

        async def go():
            client = _guarded(venue)
            client.options["uta"] = uta
            with pytest.raises(w.WriteRefused) as refused:
                await call(client)
            return str(refused.value)

        said = asyncio.run(go())
        assert said.startswith("the position watchdog only reads: refused ")
        assert venue.calls == [], f"{name} reached the transport: {venue.calls}"

    def test_the_same_writes_do_reach_an_unguarded_client(self):
        """THE CONTROL. Without the guard each of these is a real request, so
        the test above measures the guard and not a method that never sends."""
        for name, call in WRITES:
            venue = Venue(positions=[position()])

            async def go(venue=venue, call=call):
                client = venue.client()
                client.options["uta"] = False
                with pytest.raises(AssertionError, match="a write reached the transport"):
                    await call(client)

            asyncio.run(go())
            assert [method for method, _ in venue.calls] != [], name
            assert all(method != "GET" for method, _ in venue.calls[-1:]), name

    def test_a_read_passes_through(self):
        venue = Venue(positions=[position()])

        async def go():
            client = _guarded(venue)
            client.options["uta"] = False
            return await client.fetch_positions(params={"productType": "USDT-FUTURES"})

        (row,) = asyncio.run(go())
        assert row["symbol"] == SYMBOL
        assert venue.calls == [("GET", "/api/v2/mix/position/all-position")]

    @pytest.mark.parametrize("verb", ["POST", "post", "PUT", "DELETE", "PATCH", ""])
    def test_the_refusal_names_the_method_and_the_path_and_nothing_else(self, verb):
        sent = []

        class Client:
            async def fetch(self, url, method="GET", headers=None, body=None):
                sent.append(method)
                return {}

        client = w.read_only(Client())

        async def go():
            with pytest.raises(w.WriteRefused) as refused:
                await client.fetch(
                    "https://api.bitget.com/api/v2/mix/order/place-order?sign=SIGNATURE",
                    verb, {"ACCESS-KEY": "KEY-VALUE"}, '{"size":"BODY-VALUE"}')
            return str(refused.value)

        said = asyncio.run(go())
        assert said == ("the position watchdog only reads: refused "
                        f"{verb.upper()} /api/v2/mix/order/place-order")
        assert sent == []
        for secret in ("SIGNATURE", "KEY-VALUE", "BODY-VALUE"):
            assert secret not in said

    def test_a_whole_pass_sends_only_gets(self, monkeypatch, tmp_path):
        monkeypatch.setenv(w.ENABLED_ENV, "true")
        venue = Venue(positions=[position()], plans=[])
        status = asyncio.run(w.run_once(
            exchange_factory=venue.client, publisher=lambda text: None,
            state_file=tmp_path / "s.json", bot_state_dir=tmp_path))
        assert status == w.EXIT_OK
        assert venue.calls and {method for method, _ in venue.calls} == {"GET"}


# --- 2. the import closure ---------------------------------------------------

class TestNothingItImportsCanWrite:
    def test_no_module_the_watchdog_can_reach_calls_a_venue_write(self):
        reached = closure(WATCHDOG)
        assert WATCHDOG in reached and "bot.core.venues" in reached, sorted(reached)
        found = {module: hits for module, path in reached.items()
                 if (hits := write_calls(path))}
        assert found == {}, (
            "a module the position watchdog imports can place, amend or cancel "
            f"an order: {found}")

    def test_the_order_path_is_not_in_the_closure(self):
        reached = closure(WATCHDOG)
        assert [module for module in ORDER_PATH if module in reached] == []

    def test_the_walk_sees_the_writes_it_is_looking_for(self):
        """THE CONTROL. The same scan over the executor and the signed client's
        callers finds what it would refuse, so an empty result above is a
        measurement."""
        names = {name for name, _ in write_calls(ROOT / "bot" / "core" / "live_executor.py")}
        assert {"create_order", "cancel_order", "set_leverage", "set_margin_mode"} <= names, names

    def test_a_planted_import_of_the_executor_is_caught(self, tmp_path):
        """THE CONTROL for the walk: a watchdog that imports the executor two
        modules away, inside a function, is found."""
        pkg = tmp_path / "bot"
        (pkg / "core").mkdir(parents=True)
        for init in (pkg / "__init__.py", pkg / "core" / "__init__.py"):
            init.write_text("", encoding="utf-8")
        (pkg / "position_watchdog.py").write_text(
            "from bot.helper import read\n", encoding="utf-8")
        (pkg / "helper.py").write_text(
            "def read():\n    from .core import live_executor\n    return live_executor\n",
            encoding="utf-8")
        (pkg / "core" / "live_executor.py").write_text(
            "async def go(exchange):\n    await exchange.create_order('X', 'market', 'buy', 1)\n",
            encoding="utf-8")
        reached = closure(WATCHDOG, tmp_path)
        assert "bot.core.live_executor" in reached, sorted(reached)
        assert write_calls(reached["bot.core.live_executor"]) == [("create_order", 2)]


# --- 3. the module itself ----------------------------------------------------

class TestWhatTheModuleAsksOfItsClient:
    def _tree(self):
        return ast.parse((ROOT / "bot" / "position_watchdog.py").read_text(encoding="utf-8"))

    def test_it_asks_for_three_reads_and_a_close(self):
        asked = sorted({
            node.func.attr for node in ast.walk(self._tree())
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "exchange"})
        assert asked == ["close", "fetch_balance", "fetch_open_orders", "fetch_positions"]

    def test_it_names_no_write_anywhere_in_its_code(self):
        named = sorted({
            name for node in ast.walk(self._tree())
            for name in ([node.attr] if isinstance(node, ast.Attribute) else
                         [node.id] if isinstance(node, ast.Name) else [])
            if WRITE_CALL.match(name)})
        assert named == []

    def test_it_builds_no_engine_and_no_executor(self):
        names = {node.id for node in ast.walk(self._tree()) if isinstance(node, ast.Name)}
        assert not names & {"RuneClawEngine", "LiveExecutor", "BitgetV3Client"}


# --- the unit and the timer are a proposal -----------------------------------

UNITS = ROOT / "scripts" / "systemd"


def _unit(name):
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read(UNITS / name, encoding="utf-8")
    return parser


class TestTheUnitFiles:
    def test_the_service_is_one_pass_of_the_watchdog(self):
        unit = _unit("runeclaw-position-watchdog.service")
        service = unit["Service"]
        assert service["Type"] == "oneshot"
        assert service["ExecStart"].endswith("/.venv/bin/python -m bot.position_watchdog")
        # A oneshot started by a timer: nothing restarts it and nothing enables it.
        assert "Restart" not in service
        assert not unit.has_section("Install")
        # It only reads: the other units' deploy step is not run from this one.
        assert "ExecStartPre" not in service
        assert service["NoNewPrivileges"] == "true"

    def test_the_timer_runs_it_every_default_interval(self):
        timer = _unit("runeclaw-position-watchdog.timer")
        assert timer["Timer"]["Unit"] == "runeclaw-position-watchdog.service"
        assert timer["Timer"]["OnUnitActiveSec"] == f"{w.DEFAULT_INTERVAL_MIN}min"
        assert w.DEFAULT_INTERVAL_MIN == 5
        assert timer["Install"]["WantedBy"] == "timers.target"

    @pytest.mark.parametrize("path", [
        "scripts/systemd/runeclaw-status.sh", "scripts/launch_all.sh.template",
        "scripts/bot_status.py", "deploy.sh"])
    def test_nothing_in_the_repository_installs_or_starts_it(self, path):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "position-watchdog" not in text and "position_watchdog" not in text

    def test_the_proposal_is_where_the_unit_says_it_is(self):
        named = "docs/POSITION_WATCHDOG_PROPOSAL.md"
        for unit in ("runeclaw-position-watchdog.service", "runeclaw-position-watchdog.timer"):
            assert named in (UNITS / unit).read_text(encoding="utf-8")
        assert (ROOT / named).is_file()
