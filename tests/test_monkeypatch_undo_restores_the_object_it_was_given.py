"""`monkeypatch.undo()` must hand back the object it was given.

THE DEFECT. `MonkeyPatch.setattr(obj, name, value)` records the old value as
`getattr(obj, name, notset)`. For a plain instance that lookup SUCCEEDS
through the class, so `undo()` takes the `value is not notset` branch and does
`setattr(obj, name, <bound method>)` — writing into the INSTANCE's `__dict__`
a name that only ever lived on the class. From then on the instance shadows
its own type, for the rest of the session, and every later CLASS-level patch
of that name is silently ignored for that one object.

WHAT IT COST. `test_cross_venue_funding.py::test_funding_command_renders_all_venues`
patches the module singleton `cross_venue.CROSS_VENUE` on `states_for`.
`test_the_funding_card_says_which_venues_it_read.py::TestTheHandlerIsWired`
patches `CrossVenueFunding.states_for` — the class — and `_cmd_funding` reads
the singleton. Second file, second test: the plant never ran, the real reader
went to the network, and two tests asserted against whatever bybit and
hyperliquid answered. `ci_test_gate` re-ran each alone, saw it pass, and filed
it "passes alone (flaky/order-dependent)" on every run for as long as it
existed — which is the one part of that gate that can hide a leak, doing it.

The containment is in `tests/conftest.py`; this file drives it. Every claim
here is a DRIVE: patch, undo, and read the object back. A scan of the
correction cannot see whether an undo undid anything, which is the only thing
being asked.
"""
from __future__ import annotations

import asyncio
import inspect
import sys
import types

import pytest
from _pytest.monkeypatch import MonkeyPatch

import tests.conftest as conftest


class Thing:
    """A plain object whose method lives on its class — the shape the defect
    needs, and the shape of every module-level singleton in this tree."""

    CONSTANT = "class value"

    def name(self) -> str:
        return "the class's"


class Slotted:
    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value = 1


class WithProperty:
    def __init__(self) -> None:
        self._v = "initial"

    @property
    def v(self) -> str:
        return self._v

    @v.setter
    def v(self, new: str) -> None:
        self._v = new


def _fresh() -> MonkeyPatch:
    """A MonkeyPatch of our own, undone by hand. The `monkeypatch` FIXTURE is
    undone at teardown, which is after every assertion here would have run."""
    return MonkeyPatch()


class TestTheArtefactIsGone:
    def test_a_patch_that_shadows_the_class_leaves_nothing_behind(self):
        obj = Thing()
        assert "name" not in vars(obj), "precondition: it lives on the class"

        mp = _fresh()
        mp.setattr(obj, "name", lambda: "the stub's")
        assert obj.name() == "the stub's"
        mp.undo()

        assert "name" not in vars(obj), (
            "undo re-SET the class's bound method onto the instance; it now "
            "shadows the class permanently:\n  " + repr(vars(obj)))
        assert obj.name() == "the class's"

    def test_a_class_level_plant_reaches_the_object_after_an_undo(self):
        """The consequence, which is what actually broke: a shadowed instance
        cannot see a later class-level patch."""
        obj = Thing()
        mp = _fresh()
        mp.setattr(obj, "name", lambda: "the stub's")
        mp.undo()

        original = Thing.name
        Thing.name = lambda _self: "the plant's"
        try:
            assert obj.name() == "the plant's"
        finally:
            Thing.name = original

    def test_it_holds_for_a_plain_class_attribute_too(self):
        """Not only methods. A class-level constant takes the same route."""
        obj = Thing()
        mp = _fresh()
        mp.setattr(obj, "CONSTANT", "patched")
        mp.undo()
        assert "CONSTANT" not in vars(obj)
        assert obj.CONSTANT == "class value"


class TestWhatIsNotTouched:
    """The correction fires ONLY where the patch created the shadow. Every
    other target keeps the reading pytest already gives it — a containment
    that is wider than its subject is a second defect."""

    def test_a_real_instance_attribute_is_restored_not_deleted(self):
        obj = Thing()
        obj.name = lambda: "the instance's own"
        mp = _fresh()
        mp.setattr(obj, "name", lambda: "the stub's")
        mp.undo()
        assert "name" in vars(obj), "this one WAS on the instance"
        assert obj.name() == "the instance's own"

    def test_patching_a_class_keeps_pytests_own_reading(self):
        class Sub(Thing):
            pass

        assert "name" not in vars(Sub), "Sub inherits it"
        mp = _fresh()
        mp.setattr(Sub, "name", lambda _self: "the stub's")
        mp.undo()
        # pytest reads `target.__dict__.get(name, notset)` for a class, so it
        # already deletes here. The correction must not double-handle it.
        assert "name" not in vars(Sub)
        assert Sub().name() == "the class's"

    def test_patching_a_module_is_unchanged(self):
        mod = types.ModuleType("_runeclaw_probe_module")
        mod.thing = "original"
        mp = _fresh()
        mp.setattr(mod, "thing", "patched")
        mp.undo()
        assert mod.thing == "original"

    def test_a_pep562_module_gets_its_lazy_attribute_back(self):
        """The one case where the correction reaches a MODULE, and where it is
        an improvement rather than a no-op. A module with `__getattr__`
        answers a name that is not in its `vars()`, so stock pytest's undo
        MATERIALISES it — the lazy hook never runs again. The comment in
        conftest claims this; driving it is what makes the claim a check."""
        mod = types.ModuleType("_runeclaw_probe_lazy")
        calls = []

        def _getattr(name):
            if name == "lazy":
                calls.append(name)
                return "from __getattr__"
            raise AttributeError(name)

        mod.__getattr__ = _getattr
        assert mod.lazy == "from __getattr__" and "lazy" not in vars(mod)

        mp = _fresh()
        mp.setattr(mod, "lazy", "patched")
        mp.undo()

        assert "lazy" not in vars(mod), (
            "undo materialised the lazy attribute; __getattr__ is dead for it")
        before = len(calls)
        assert mod.lazy == "from __getattr__"
        assert len(calls) == before + 1, "the hook did not run again"

    def test_a_property_setter_is_not_read_as_a_shadow(self):
        """`setattr` here runs the SETTER and writes `_v`, so `v` never enters
        the instance `__dict__` — the correction must not try to delete it."""
        obj = WithProperty()
        mp = _fresh()
        mp.setattr(obj, "v", "patched")
        assert obj.v == "patched"
        mp.undo()
        assert obj.v == "initial"

    def test_an_object_with_no_instance_dict_is_left_alone(self):
        """`__slots__`: nothing was measured, so nothing is corrected."""
        obj = Slotted()
        mp = _fresh()
        mp.setattr(obj, "value", 99)
        assert obj.value == 99
        mp.undo()
        assert obj.value == 1


class TestTheOtherArgumentShapes:
    def test_the_dotted_string_form_is_covered(self):
        mod = types.ModuleType("_runeclaw_probe_dotted")
        mod.SINGLETON = Thing()
        sys.modules["_runeclaw_probe_dotted"] = mod
        try:
            mp = _fresh()
            mp.setattr("_runeclaw_probe_dotted.SINGLETON.name",
                       lambda: "the stub's")
            mp.undo()
            assert "name" not in vars(mod.SINGLETON)
            assert mod.SINGLETON.name() == "the class's"
        finally:
            sys.modules.pop("_runeclaw_probe_dotted", None)

    def test_two_patches_of_one_name_undo_to_the_original(self):
        obj = Thing()
        mp = _fresh()
        mp.setattr(obj, "name", lambda: "first")
        mp.setattr(obj, "name", lambda: "second")
        assert obj.name() == "second"
        mp.undo()
        assert "name" not in vars(obj)
        assert obj.name() == "the class's"

    def test_raising_false_on_a_name_that_exists_nowhere_still_deletes(self):
        obj = Thing()
        mp = _fresh()
        mp.setattr(obj, "invented", "x", raising=False)
        mp.undo()
        assert "invented" not in vars(obj)


class TestTheSingletonThatBroke:
    """Driven on the real object, because the defect was never about `Thing`."""

    def test_a_class_plant_reaches_CROSS_VENUE_after_an_instance_patch(self):
        from bot.core import cross_venue as cv

        assert "states_for" not in vars(cv.CROSS_VENUE), (
            "the singleton is carrying a shadowing attribute before this test "
            "even runs — something leaked it earlier in the session")

        async def _stub(_sym):
            return []

        mp = _fresh()
        mp.setattr(cv.CROSS_VENUE, "states_for", _stub)
        mp.undo()

        assert "states_for" not in vars(cv.CROSS_VENUE)

        planted = ["PLANTED"]

        async def _planted(_self, _sym):
            return planted

        original = cv.CrossVenueFunding.states_for
        cv.CrossVenueFunding.states_for = _planted
        try:
            got = asyncio.run(cv.CROSS_VENUE.states_for("BTC"))
        finally:
            cv.CrossVenueFunding.states_for = original

        assert got is planted, (
            "the class-level plant did not reach the singleton, so the real "
            "reader ran — which is a LIVE VENUE FETCH from a unit test")


class TestTheContainmentIsInstalled:
    def test_the_wrapper_is_in_place(self):
        assert getattr(MonkeyPatch.setattr, "_runeclaw_honest_undo", False), (
            "conftest's correction is not installed, so every claim in this "
            "file is being made about stock pytest")

    def test_installing_twice_does_not_stack_wrappers(self):
        before = MonkeyPatch.setattr
        conftest._install_an_honest_monkeypatch_undo()
        assert MonkeyPatch.setattr is before

    def test_a_pytest_without_the_shape_is_not_a_pass(self):
        """A containment that cannot be installed must ABORT the run, not
        install nothing quietly. `ruff_gate.check_version` is the precedent:
        a gate that COULD NOT CHECK is not a gate that passed."""
        stub = types.ModuleType("_pytest.monkeypatch")   # no derive_importpath
        real = sys.modules["_pytest.monkeypatch"]
        sys.modules["_pytest.monkeypatch"] = stub
        try:
            with pytest.raises(pytest.UsageError) as caught:
                conftest.pytest_configure(None)
        finally:
            sys.modules["_pytest.monkeypatch"] = real
        assert "monkeypatch" in str(caught.value)
        assert "NOT a pass" in str(caught.value)

    def test_configure_refuses_a_live_store_before_it_installs_anything(self):
        """Order: the destructive check first. A run that must not happen must
        not first mutate the test framework."""
        src = inspect.getsource(conftest.pytest_configure)
        assert (src.index("_refuse_a_live_store")
                < src.index("_install_an_honest_monkeypatch_undo"))


class TestTheSelfTest:
    """The correction rewrites `MonkeyPatch._setattr`'s last entry. Nothing
    else here checks that pytest still keeps its undo stack that way, so the
    install DRIVES the correction once against the installed pytest. A
    containment that is present and correcting nothing is a containment
    reporting success over the leak it exists to prevent."""

    def test_it_passes_against_the_real_pytest(self):
        conftest._prove_the_undo_is_honest(MonkeyPatch)

    def test_the_install_runs_it(self):
        """A source read, and it is the honest instrument here: the claim is
        WIRING — that the install reaches the self-test — and the install is
        idempotent, so a drive would have to unpick the wrapper it just put
        in. An AST CALL node, never a substring: `_cmd_trade` is this repo's
        record of a literal that exists satisfying an assertion about code
        that runs."""
        import ast
        tree = ast.parse(inspect.getsource(conftest._install_an_honest_monkeypatch_undo))
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "_prove_the_undo_is_honest" in called, (
            "the correction installs itself and never checks that it took")

    def test_it_fails_when_the_undo_leaves_a_shadow(self):
        class StockUndo:
            """pytest as it ships: undo re-SETS what getattr handed back."""

            def __init__(self):
                self._saved = []

            def setattr(self, obj, name, value):
                self._saved.append((obj, name, getattr(obj, name)))
                setattr(obj, name, value)

            def undo(self):
                for obj, name, old in reversed(self._saved):
                    setattr(obj, name, old)

        with pytest.raises(RuntimeError, match="shadowing instance attribute"):
            conftest._prove_the_undo_is_honest(StockUndo)

    def test_it_fails_when_the_patch_does_not_take(self):
        class Inert:
            def setattr(self, obj, name, value):
                pass

            def undo(self):
                pass

        with pytest.raises(RuntimeError, match="did not take"):
            conftest._prove_the_undo_is_honest(Inert)

    def test_it_fails_when_undo_deletes_what_was_really_there(self):
        """The second direction. A stub that deletes unconditionally passes
        the shadow check and would destroy every real instance attribute a
        test patches — so the self-test drives that too."""
        class DeletesEverything:
            def setattr(self, obj, name, value):
                setattr(obj, name, value)

            def undo(self):
                pass            # the guard below is what a delete looks like

        class Deleter(DeletesEverything):
            def setattr(self, obj, name, value):
                self._t = (obj, name)
                setattr(obj, name, value)

            def undo(self):
                obj, name = self._t
                obj.__dict__.pop(name, None)

        with pytest.raises(RuntimeError, match="wider than the shadow"):
            conftest._prove_the_undo_is_honest(Deleter)


class TestTheReading:
    """`_own_dict_has` is three-valued, and the third value is why a slotted
    object is left alone rather than guessed about."""

    def test_present_absent_and_unmeasurable(self):
        obj = Thing()
        assert conftest._own_dict_has(obj, "name") is False
        obj.name = lambda: "x"
        assert conftest._own_dict_has(obj, "name") is True
        assert conftest._own_dict_has(Slotted(), "value") is None

    def test_a_class_is_declined_by_its_own_mappingproxy(self):
        """Why the correction carries no `inspect.isclass` branch: a class's
        `__dict__` is a mappingproxy, not a dict, so the reading already
        answers "nothing measured" and the correction never fires. Driven,
        because a branch written for a case that cannot reach it is a claim
        that there is a check — the mutation round is what said so."""
        assert not isinstance(vars(Thing), dict)
        assert conftest._own_dict_has(Thing, "name") is None
        assert conftest._own_dict_has(Thing, "invented") is None
