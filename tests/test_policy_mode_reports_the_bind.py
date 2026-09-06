"""`/policy mode enforce` said ✅ whether or not anything was enforced.

    bound = self.engine.set_intent_policy_mode(m)      # ← discarded
    ...
    await self._send(update, f"✅ Policy mode → <b>{m}</b>.{tail}")

`set_intent_policy_mode` returns THE BOUND POLICY — what the risk gate will
actually consult — and `None` when nothing bound. `write_intent_policy` fails
OPEN, so `None` covers a missing file, an invalid spec, and a COMPILE FAULT.

The `tail` the handler did compute covers only the benign cause (the
enforcement flag being off). The one it could not report is the one that
matters: an operator asks for enforce, the spec does not compile, the engine
is left consulting NO intent policy at all, and the bot replies ✅.

Same shape as `seal_decision` running outside `if not live_failed:` — a value
computed correctly and never read is not better than one never computed, and
it is harder to see because the code looks finished.
"""
import io

from tests.source_scan import code_only


def _branch():
    """The `/policy mode` arm of `_cmd_policy`, comments stripped.

    `_cmd_policy` lives in bot/skills/guardian_commands.py since the second
    slice of the handler split; `tests/test_guardian_commands_split.py` pins
    that the handler still reaches it there.
    """
    code = code_only(io.open("bot/skills/guardian_commands.py",
                             encoding="utf-8").read())
    i = code.index("bound = self.engine.set_intent_policy_mode(m)")
    # Sliced to the END of the arm (its `return`), not by a character count:
    # a 2000-char window stopped short of the success reply and this file's
    # own assertions failed on a window bug rather than on the code.
    j = code.index('f"✅ Policy mode', i)
    return code[i:j + 200]


def test_the_bind_result_is_read():
    b = _branch()
    assert "if bound is None" in b


def test_a_failed_bind_does_not_report_success():
    b = _branch()
    i = b.index("if bound is None")
    # The refusal must come BEFORE the success reply, and return.
    j = b.index("Policy mode →")
    assert i < j
    assert "return" in b[i:j]


def test_the_refusal_says_the_gate_is_consulting_nothing():
    # "saved" alone would be true and useless: the file WAS written. What the
    # operator needs is that enforcement is not in force.
    b = _branch()
    assert "NOTHING IS BOUND" in b
    assert "consulting no intent policy" in b


def test_mode_off_is_not_treated_as_a_failure():
    # `off` legitimately binds nothing. Warning there would train the operator
    # to ignore the warning.
    assert 'm != "off"' in _branch()


def test_the_flag_off_case_keeps_its_gentler_note():
    # An unset INTENT_POLICY_ENABLED is benign and already had honest words;
    # this fix must not replace them with the alarming ones.
    b = _branch()
    assert "INTENT_POLICY_ENABLED is off" in b
    assert "saved but dormant" in b


def test_the_success_reply_is_no_longer_unconditional():
    b = _branch()
    # Reachable only after the None check has been passed.
    assert b.index("if bound is None") < b.index('f"✅ Policy mode')
