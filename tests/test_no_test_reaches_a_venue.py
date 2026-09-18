"""A test that loses its stub must not become a venue read.

WHAT THIS IS ABOUT. `tests/test_scan_reads_the_executors_record.py` stubs the
closed-trade file and asserts about the record it holds. Further into the same
`_fetch_live_exchange_data`, a real `ccxt_sync.bitget({...})` is built and
`fetch_balance` is called against api.bitget.com — three times with its
retries — and every one of that file's eight tests passed, because the only
field that read feeds is asserted by none of them and the read sits inside a
broad `except`. Driven over the whole suite, twelve tests in four files reached
six hosts.

None of the twelve ASSERTS against a venue. That is the quiet half: a test that
asserts against a live venue goes red the first time the venue disagrees, where
a test that merely reaches one is slow, nondeterministic and green — and when it
does go red, `ci_test_gate`'s flake filter re-runs it alone, the venue answers
that time, and the result is filed `~ passes alone (flaky/order-dependent)`.

The containment is in `tests/conftest.py`; this file drives it. The reading, the
ledger and the sentence are each exercised directly, the refusal is exercised
through the REAL socket module as it is installed in this very session, and the
install's own self-test is driven against stubs in every direction it claims to
check — because a containment that is present and refusing nothing is a
containment reporting success over the leak it exists to prevent.
"""
from __future__ import annotations

import ast
import errno
import os
import socket
import sys
from pathlib import Path

import pytest

from tests.source_scan import code_only

CONFTEST = (Path(__file__).resolve().parent / "conftest.py").resolve()
ROOT = CONFTEST.parent.parent


def _the_conftest_pytest_loaded():
    """ONE FILE, TWO MODULE OBJECTS — and the import system makes the copy.

    `tests/` has no `__init__.py`, so pytest imports the file as the TOP-LEVEL
    module `conftest`, while `import tests.conftest` resolves through the
    namespace package and imports it AGAIN under a second name. Both are in
    `sys.modules`. That is two `_OUTBOUND` ledgers and two `_outbound_verdict`s
    for one containment, and the first draft of this file imported the one
    nothing had installed: it drained an empty ledger, reported the spy
    unasked, and left the real rows for the containment to report against the
    guard that drives it.

    So the module is taken from the INSTALLED PATCH ITSELF — `connect.__globals__`
    is the module dict the refusal really reads — which cannot name the wrong
    copy however many copies exist. The second value says whether that
    resolution succeeded; a guard reading a module nothing patched is a guard
    claiming a check nobody made.
    """
    g = getattr(socket.socket.connect, "__globals__", {})
    name = g.get("__name__")
    mod = sys.modules.get(name) if name else None
    where = getattr(mod, "__file__", None)
    if where and Path(where).resolve() == CONFTEST:
        return mod, True
    import tests.conftest as by_import
    return by_import, False


conftest, CONTAINMENT_INSTALLED = _the_conftest_pytest_loaded()


@pytest.fixture
def refusals():
    """This file reaches a documentation address ON PURPOSE.

    Without this the containment would correctly report the guard that drives
    it, so the rows are drained here — and the ledger's own stamp is handed
    back, because a test that leaves it pointing at nothing would file the NEXT
    test's connect under no test at all.
    """
    stamp = conftest._OUTBOUND.nodeid
    conftest._OUTBOUND.drain_all()
    yield conftest._OUTBOUND
    conftest._OUTBOUND.drain_all()
    conftest._OUTBOUND.nodeid = stamp


# ── the reading ──────────────────────────────────────────────────────
class TestTheReading:
    """Four words, and two of them are allowed for DIFFERENT reasons."""

    def test_a_socket_that_is_not_ip_is_not_a_venue_claim(self):
        # AF_UNIX carries a path, not a host and a port. Nothing about a venue
        # can be claimed of it, so nothing is refused — a measurement, not a
        # fall-through.
        word, host, port = conftest._outbound_verdict(socket.AF_UNIX, "/tmp/x.sock")
        assert word == "not-ip"
        assert (host, port) == ("", -1)

    @pytest.mark.parametrize("host", [
        "127.0.0.1", "127.0.0.53", "127.1.2.3", "::1", "localhost",
        "LOCALHOST", "localhost.localdomain", "ip6-localhost", "ip6-loopback",
    ])
    def test_loopback_is_allowed_by_literal_and_by_name(self, host):
        fam = socket.AF_INET6 if ":" in host else socket.AF_INET
        word, got, port = conftest._outbound_verdict(fam, (host, 8080))
        assert word == "local", (host, word)
        assert got == host and port == 8080

    def test_a_scope_id_belongs_to_the_interface_not_the_address(self):
        assert conftest._outbound_verdict(socket.AF_INET6, ("::1%lo", 1, 0, 2))[0] == "local"
        assert conftest._outbound_verdict(socket.AF_INET6, ("fe80::1%eth0", 1, 0, 2))[0] == "remote"

    def test_the_scope_is_read_by_ipaddress_and_not_by_a_line_here(self):
        """Why there is no `%` strip in the reading.

        A first draft split the scope off before parsing, and the mutation that
        removed the split changed no verdict on any input a socket can produce
        — an EQUIVALENT MUTANT, which is the round saying the code claims a
        check it does not make. The line is gone and the property it was
        standing in for is driven here, so the day `ipaddress` stops reading a
        scope this fails rather than the containment quietly refusing `::1` on
        a machine that spells its loopback with an interface.
        """
        import ipaddress
        assert ipaddress.ip_address("::1%lo").is_loopback is True
        assert ipaddress.ip_address("fe80::1%eth0").is_loopback is False

    @pytest.mark.parametrize("host", [
        "api.bitget.com", "api.bybit.com", "www.coindesk.com",
        "8.8.8.8", "10.0.0.5", "192.168.1.9", "0.0.0.0", "2606:4700::1111",
    ])
    def test_everything_else_is_remote(self, host):
        fam = socket.AF_INET6 if host.count(":") > 1 else socket.AF_INET
        word, got, port = conftest._outbound_verdict(fam, (host, 443))
        assert word == "remote", (host, word)
        assert got == host and port == 443

    def test_a_bytes_host_is_read_rather_than_refused(self):
        # ccxt and aiohttp both hand `connect` a str, but the socket API takes
        # bytes and a reading that refused them would be a false accusation.
        assert conftest._outbound_verdict(socket.AF_INET, (b"127.0.0.1", 80))[0] == "local"
        assert conftest._outbound_verdict(socket.AF_INET, (b"api.bybit.com", 443))[0] == "remote"

    @pytest.mark.parametrize("address", [
        (), ("", 443), (None, 443), (b"\xff\xfe", 443), 1234, "not-a-tuple",
    ])
    def test_an_address_it_cannot_place_is_refused_not_allowed(self, address):
        # UNREADABLE IS NOT LOCAL. Reading it as loopback would be the
        # failed-read-as-allowed shape, on the one gate whose job is to refuse.
        word, host, _port = conftest._outbound_verdict(socket.AF_INET, address)
        assert word == "unreadable", (address, word)
        assert host.startswith("<") and host.endswith(">")

    def test_a_port_that_cannot_be_read_is_an_absence(self):
        _word, _host, port = conftest._outbound_verdict(
            socket.AF_INET, ("api.bitget.com", "https"))
        assert port == -1
        line = conftest._outbound_row_line("remote", "api.bitget.com", -1, "connect")
        assert "api.bitget.com" in line
        assert "-1" not in line          # never printed as a port of minus one


# ── the ledger ───────────────────────────────────────────────────────
class TestTheLedger:
    def test_the_stamp_is_taken_at_connect_time(self):
        """Not at report time.

        `pytest_runtest_teardown` runs ALONGSIDE the hook that invokes the
        fixture finalizers, so a snapshot taken there precedes them and files
        every artefact under the NEXT test — the mis-attribution the monkeypatch
        probe hit. The ledger stamps when the connect happens.
        """
        led = conftest._OutboundLedger()
        led.nodeid = "tests/a.py::test_a"
        led.note("remote", "api.bitget.com", 443, "connect")
        led.nodeid = "tests/b.py::test_b"
        led.note("remote", "api.bybit.com", 443, "connect")
        assert [r[2] for r in led.drain("tests/a.py::test_a")] == ["api.bitget.com"]
        assert [r[2] for r in led.drain("tests/b.py::test_b")] == ["api.bybit.com"]

    def test_draining_one_test_leaves_the_others(self):
        led = conftest._OutboundLedger()
        led.nodeid = "tests/a.py::test_a"
        led.note("remote", "one", 1, "connect")
        led.nodeid = None
        led.note("remote", "outside", 2, "connect")
        assert len(led.drain("tests/a.py::test_a")) == 1
        left = led.drain_all()
        assert [r[0] for r in left] == [None]
        assert led.drain_all() == []


# ── the sentence ─────────────────────────────────────────────────────
class TestTheSentence:
    def _text(self, rows):
        return conftest._reached_the_network_text("tests/x.py::test_y", rows)

    def test_it_names_the_test_the_address_and_both_remedies(self):
        out = self._text([(None, "remote", "api.bitget.com", 443, "connect")])
        assert "tests/x.py::test_y" in out
        assert "api.bitget.com:443" in out
        assert "Stub the seam" in out
        assert conftest._NETWORK_OVERRIDE_ENV in out

    def test_it_says_the_code_under_test_saw_an_ordinary_refusal(self):
        # Without this the reader's next move is to look for a broken network.
        out = self._text([(None, "remote", "api.bybit.com", 443, "connect")])
        assert "ECONNREFUSED" in out

    def test_an_unplaceable_address_reads_differently_from_a_venue(self):
        venue = self._text([(None, "remote", "api.bitget.com", 443, "connect")])
        unread = self._text([(None, "unreadable", "<int>", -1, "connect")])
        assert "could not place" in unread
        assert "could not place" not in venue

    def test_connect_ex_is_named_and_connect_is_not(self):
        assert "connect_ex" in conftest._outbound_row_line(
            "remote", "h", 1, "connect_ex")
        assert "(connect)" not in conftest._outbound_row_line(
            "remote", "h", 1, "connect")

    def test_the_count_agrees_with_the_rows(self):
        out = self._text([(None, "remote", "a", 1, "connect"),
                          (None, "remote", "b", 2, "connect")])
        assert "2 refused connects" in out
        one = self._text([(None, "remote", "a", 1, "connect")])
        assert "1 refused connect:" in one


# ── the refusal, through the socket module as installed ──────────────
class TestTheRefusalIsInstalled:
    def setup_method(self):
        if os.environ.get(conftest._NETWORK_OVERRIDE_ENV):
            pytest.skip(f"{conftest._NETWORK_OVERRIDE_ENV} is set: the "
                        f"containment is off for this run on purpose, and a "
                        f"guard that passed anyway would be claiming a check "
                        f"nobody made")
        assert CONTAINMENT_INSTALLED, (
            "socket.socket.connect does not come from tests/conftest.py, so "
            "every check in this class would be reading a module nothing "
            "patched — which is not a pass")

    def test_the_ledger_this_file_drains_is_the_one_the_patch_writes(self):
        """The finding that made this class possible.

        Asserting identity rather than equality: two module objects for one
        file agree on every pure function and share no state at all, so a
        guard that imported the wrong one passes every reading test in this
        file and measures nothing about the containment.
        """
        assert conftest._OUTBOUND is socket.socket.connect.__globals__["_OUTBOUND"]
        import tests.conftest as by_import
        if by_import is not conftest:
            assert by_import._OUTBOUND is not conftest._OUTBOUND

    def test_this_session_is_running_the_patched_connect(self):
        # Reachability, not shape: the containment claims nothing unless the
        # install really ran in the process the suite is running in.
        assert getattr(socket.socket.connect, "_runeclaw_no_venue", False)
        assert getattr(socket.socket.connect_ex, "_runeclaw_no_venue", False)

    def test_a_venue_connect_is_refused_as_an_ordinary_oserror(self, refusals):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(ConnectionRefusedError) as exc:
                s.connect(("198.51.100.7", 443))
        finally:
            s.close()
        assert isinstance(exc.value, OSError)
        assert exc.value.errno == errno.ECONNREFUSED

    def test_the_row_is_stamped_with_the_test_that_made_it(self, request, refusals):
        """The stamp comes from `pytest_runtest_logstart`, not from the report.

        Without it every row is filed under no test at all, the per-test
        reporter finds nothing to say, and the whole thing degrades to a
        session-level notice — which is a report nobody is failed by.
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(ConnectionRefusedError):
                s.connect(("198.51.100.7", 443))
        finally:
            s.close()
        assert [r[0] for r in refusals.drain_all()] == [request.node.nodeid]

    def test_the_stamp_is_cleared_when_the_test_ends(self, refusals):
        """A connect made between tests is not the last test's.

        Driven through the hooks rather than reasoned about: the row made
        after `logfinish` carries no stamp, which is what sends it to the
        session backstop under `<outside any test>` instead of accusing
        whichever test happened to have run last.
        """
        conftest.pytest_runtest_logstart("tests/planted.py::test_x", None)
        conftest._OUTBOUND.note("remote", "during", 1, "connect")
        conftest.pytest_runtest_logfinish("tests/planted.py::test_x", None)
        conftest._OUTBOUND.note("remote", "between", 2, "connect")
        assert [(r[0], r[2]) for r in refusals.drain_all()] == [
            ("tests/planted.py::test_x", "during"), (None, "between")]

    def test_the_code_under_test_can_still_catch_it(self, refusals):
        """The whole reason it is not a BaseException.

        Every venue reader in this tree catches broadly; a refusal that escaped
        those handlers would change the control flow of the code being driven,
        which is a different test.
        """
        def reader():
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.connect(("198.51.100.7", 443))
                return "read"
            except Exception:                        # noqa: BLE001 - the point
                return "handled"
            finally:
                s.close()

        assert reader() == "handled"

    def test_connect_ex_answers_rather_than_raises(self, refusals):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            rc = s.connect_ex(("198.51.100.7", 443))
        finally:
            s.close()
        assert rc == errno.ECONNREFUSED
        assert [r[4] for r in refusals.drain_all()] == ["connect_ex"]

    def test_loopback_still_connects(self, refusals):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            client.settimeout(5)
            client.connect(server.getsockname())
            assert client.getpeername()[0] == "127.0.0.1"
        finally:
            client.close()
            server.close()
        assert refusals.drain_all() == []

    def test_the_installed_connect_asks_the_reading(self, monkeypatch, refusals):
        """One walk, proved by patching it.

        A second copy of the loopback rule inside the socket patch would agree
        with every fixture here and diverge on the first edit to either.
        """
        seen = []

        def _spy(family, address):
            seen.append(address)
            return "remote", "spied", 1

        monkeypatch.setattr(conftest, "_outbound_verdict", _spy)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(ConnectionRefusedError):
                s.connect(("127.0.0.1", 9))          # loopback, refused by the spy
        finally:
            s.close()
        assert seen == [("127.0.0.1", 9)]
        assert [r[2] for r in refusals.drain_all()] == ["spied"]

    def test_installing_twice_does_not_double_wrap(self):
        before = socket.socket.connect
        conftest._refuse_outbound_connections()
        assert socket.socket.connect is before


# ── the install's own self-test ──────────────────────────────────────
class _StubSocket:
    """The narrowest stand-in the self-test can be driven against."""

    AF_INET = socket.AF_INET
    AF_INET6 = socket.AF_INET6
    SOCK_STREAM = socket.SOCK_STREAM

    def __init__(self, refuse_remote=True, refuse_loopback=False, record=True):
        self.refuse_remote = refuse_remote
        self.refuse_loopback = refuse_loopback
        self.record = record

    def socket(self, family, kind):                  # noqa: A003 - mirrors socket
        return _StubSock(self)

    def __call__(self, family, kind):
        return _StubSock(self)


class _StubSock:
    def __init__(self, cfg):
        self.cfg = cfg
        self._name = ("127.0.0.1", 45454)

    def connect(self, address):
        local = conftest._outbound_verdict(socket.AF_INET, address)[0] == "local"
        refuse = self.cfg.refuse_loopback if local else self.cfg.refuse_remote
        if not refuse:
            return None
        if self.cfg.record:
            conftest._OUTBOUND.note("remote", address[0], address[1], "connect")
        raise conftest._RefusedOutbound(errno.ECONNREFUSED, "stub refusal")

    def bind(self, address):
        pass

    def listen(self, backlog):
        pass

    def settimeout(self, t):
        pass

    def getsockname(self):
        return self._name

    def close(self):
        pass


class TestTheSelfTest:
    """It has to bite in BOTH directions, and the second is the expensive one."""

    def setup_method(self):
        conftest._OUTBOUND.drain_all()

    def teardown_method(self):
        conftest._OUTBOUND.drain_all()

    def test_it_fails_when_nothing_is_refused(self):
        stub = _StubSocket(refuse_remote=False)
        with pytest.raises(RuntimeError, match="let a non-loopback connect through"):
            conftest._prove_the_refusal_is_honest(stub)

    def test_it_fails_when_the_refusal_records_nothing(self):
        stub = _StubSocket(record=False)
        with pytest.raises(RuntimeError, match="recorded nothing"):
            conftest._prove_the_refusal_is_honest(stub)

    def test_it_fails_when_loopback_is_refused_too(self):
        """A rule that refuses EVERYTHING passes the first direction.

        It would also take down every test that stands up a local server, which
        is why this direction is driven rather than reasoned about.
        """
        stub = _StubSocket(refuse_loopback=True)
        with pytest.raises(RuntimeError, match="refused loopback"):
            conftest._prove_the_refusal_is_honest(stub)

    def test_it_fails_when_a_loopback_connect_is_recorded(self):
        class _RecordsLoopback(_StubSocket):
            pass

        stub = _RecordsLoopback()
        real = _StubSock.connect

        def connect(self, address):
            if conftest._outbound_verdict(socket.AF_INET, address)[0] == "local":
                conftest._OUTBOUND.note("remote", address[0], address[1], "connect")
                return None
            return real(self, address)

        _StubSock.connect = connect
        try:
            with pytest.raises(RuntimeError, match="recorded as reaching a venue"):
                conftest._prove_the_refusal_is_honest(stub)
        finally:
            _StubSock.connect = real

    def test_a_stub_that_raises_something_else_is_not_read_as_a_refusal(self):
        class _Wrong(_StubSock):
            def connect(self, address):
                raise TimeoutError("not the refusal")

        stub = _StubSocket()
        stub.socket = lambda family, kind: _Wrong(stub)
        with pytest.raises(RuntimeError, match="rather than the refusal"):
            conftest._prove_the_refusal_is_honest(stub)

    def test_the_honest_shape_passes(self):
        conftest._prove_the_refusal_is_honest(_StubSocket())


# ── the wiring ───────────────────────────────────────────────────────
def _conftest_ast():
    return ast.parse(CONFTEST.read_text())


class TestTheWiring:
    def test_the_install_runs_its_self_test(self):
        """A source read, and the honest instrument here for the reason the
        monkeypatch containment's twin gives: the claim is WIRING — that the
        install reaches the self-test — and the install is idempotent, so a
        drive would have to unpick the patch it has just put in. An AST CALL
        node, never a substring. The mutation round found this gap: removing
        the call left every other check in this file green.
        """
        fn = next(n for n in _conftest_ast().body
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_refuse_outbound_connections")
        called = {n.func.id for n in ast.walk(fn)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "_prove_the_refusal_is_honest" in called, (
            "the refusal installs itself and never checks that it took")

    def test_configure_installs_it(self):
        fn = next(n for n in _conftest_ast().body
                  if isinstance(n, ast.FunctionDef) and n.name == "pytest_configure")
        called = {n.func.id for n in ast.walk(fn)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "_refuse_outbound_connections" in called

    def test_the_reporting_fixture_is_declared_first(self):
        """THE ONE SCAN HERE, AND IT PINS A SHAPE NO DRIVE CAN REACH.

        Same-scope autouse fixtures set up in declaration order and finalize in
        reverse, so the reporter has to be FIRST to see a connect made in
        another fixture's teardown. That is pytest's behaviour, driven once when
        this was designed; what a unit test cannot reach is whether this file
        still relies on it from the position it needs.
        """
        autouse = []
        for node in _conftest_ast().body:
            if not isinstance(node, ast.FunctionDef):
                continue
            for dec in node.decorator_list:
                if (isinstance(dec, ast.Call)
                        and any(k.arg == "autouse" for k in dec.keywords)):
                    autouse.append(node.name)
        assert autouse[0] == "_no_test_reaches_a_venue", autouse

    def test_the_reporter_fails_the_test_and_names_it(self, refusals):
        """Drive the fixture body, because `close()` never runs the teardown.

        Throwing `GeneratorExit` at the yield skips everything after it — the
        trap a fixture drive fell into one containment ago — so the generator is
        advanced instead.
        """
        node = type("N", (), {"nodeid": "tests/planted.py::test_planted"})()
        request = type("R", (), {"node": node})()
        gen = conftest._no_test_reaches_a_venue.__wrapped__(request)
        next(gen)
        conftest._OUTBOUND.nodeid = node.nodeid
        conftest._OUTBOUND.note("remote", "api.bitget.com", 443, "connect")
        conftest._OUTBOUND.nodeid = None
        with pytest.raises(pytest.fail.Exception) as exc:
            next(gen)
        assert "tests/planted.py::test_planted" in str(exc.value)
        assert "api.bitget.com:443" in str(exc.value)

    def test_a_clean_test_is_not_failed(self):
        node = type("N", (), {"nodeid": "tests/planted.py::test_clean"})()
        request = type("R", (), {"node": node})()
        gen = conftest._no_test_reaches_a_venue.__wrapped__(request)
        next(gen)
        with pytest.raises(StopIteration):
            next(gen)

    def test_the_backstop_names_the_test_a_row_was_stamped_with(self, capsys, refusals):
        conftest._OUTBOUND.nodeid = "tests/planted.py::test_late"
        conftest._OUTBOUND.note("remote", "api.bybit.com", 443, "connect")
        conftest._OUTBOUND.nodeid = None
        conftest._OUTBOUND.note("remote", "api.bitget.com", 443, "connect")
        session = type("S", (), {"exitstatus": 0})()
        conftest.pytest_sessionfinish(session, 0)
        out = capsys.readouterr().out
        assert "tests/planted.py::test_late" in out
        assert "<outside any test>" in out
        assert session.exitstatus == 1
        assert conftest._OUTBOUND.drain_all() == []

    def test_the_backstop_says_nothing_when_there_is_nothing(self, refusals):
        session = type("S", (), {"exitstatus": 0})()
        conftest.pytest_sessionfinish(session, 0)
        assert session.exitstatus == 0


class TestTheDoor:
    def test_the_override_is_a_whole_run_decision_and_says_so(self, monkeypatch, capsys):
        installed = []
        monkeypatch.setattr(conftest, "_refuse_a_live_store", lambda: None)
        monkeypatch.setenv(conftest._NETWORK_OVERRIDE_ENV, "1")
        monkeypatch.setattr(conftest, "_refuse_outbound_connections",
                            lambda: installed.append(1))
        conftest.pytest_configure(config=None)
        assert installed == []
        out = capsys.readouterr().out
        assert conftest._NETWORK_OVERRIDE_ENV in out
        assert "NOT refused" in out

    def test_without_it_the_install_runs(self, monkeypatch):
        installed = []
        monkeypatch.setattr(conftest, "_refuse_a_live_store", lambda: None)
        monkeypatch.delenv(conftest._NETWORK_OVERRIDE_ENV, raising=False)
        monkeypatch.setattr(conftest, "_refuse_outbound_connections",
                            lambda: installed.append(1))
        conftest.pytest_configure(config=None)
        assert installed == [1]

    def test_there_is_no_per_test_marker(self):
        """Stated as a decision rather than left for a reader to wonder about.

        No test in this tree needs the network, and a marker nothing uses is a
        door painted on a wall — the shape this repo records about a card that
        names a command which does nothing.
        """
        src = CONFTEST.read_text()
        assert "add_marker" not in src
        assert "get_closest_marker" not in src


class TestTheStatedLimits:
    def test_there_is_no_connectionless_udp_in_the_tree(self):
        """The containment's coverage claim, driven rather than assumed.

        A datagram sent with no connect crosses no `connect`, so it is outside
        what this refuses — and the comment says so. The day one appears, the
        claim needs revisiting, and that should fail here rather than be
        discovered from a flake.

        The needles are spelled in halves on purpose: a guard that quotes the
        token it forbids accuses itself, and `code_only` is what keeps the
        containment's own COMMENT about this out of the count.
        """
        needles = ("SOCK_" + "DGRAM", ".send" + "to(")
        hits = []
        for d in ("bot", "scripts", "tests", "app"):
            for path in (ROOT / d).rglob("*.py"):
                text = path.read_text(errors="ignore")
                if not any(n in text for n in needles):
                    continue
                if any(n in code_only(text) for n in needles):
                    hits.append(str(path.relative_to(ROOT)))
        assert hits == [], hits

    def test_a_unix_socket_is_not_read_as_a_venue(self):
        """AF_UNIX carries a PATH, and a path indexes like a host.

        `"/run/x.sock"[0]` is `"/"` — perfectly readable — so a reading that
        looked at the address before the family would refuse every unix-socket
        connect in the process and name `/` as the venue. The family is what
        says this is not an IP connect at all.
        """
        assert conftest._outbound_verdict(
            socket.AF_UNIX, "/run/x.sock") == ("not-ip", "", -1)
        assert conftest._outbound_verdict(
            socket.AF_INET, "/run/x.sock")[0] == "unreadable"
