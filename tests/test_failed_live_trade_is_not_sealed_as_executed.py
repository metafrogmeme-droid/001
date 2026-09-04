"""A trade the venue refused is not a trade that happened.

`engine.py` computed `live_failed = execution_indicates_failure(result)` and
guarded a block with `if not live_failed:` — then let THREE reporting calls
run at the outer indent, one level out from that guard:

  * `seal_decision(outcome="EXECUTED_LIVE", is_paper=False)` — a phantom live
    fill written to the TAMPER-EVIDENT chain, the record every other check is
    judged against;
  * `learning.log_decision(decision="TRADE_ACCEPTED_LIVE")` — the calibrator
    trained on a position that never opened;
  * `_transition(AgentState.IDLE, "live trade executed")`.

The comment five lines above that `if` describes this exact bug being fixed
one layer down: the old prefix list "missed REFUSED: / EXECUTION BLOCKED:"
and "blocked trades were sealed to the audit chain as phantom live fills".
The CLASSIFICATION was cured and nothing downstream consulted it. A guard
that is computed correctly and never read is the same defect as one that is
absent, and it is harder to see.

Nothing is skipped on failure — an approved entry that did not fill is
exactly what an audit chain is for, and a missing record is its own false
claim. The three carry the real outcome instead.
"""
import io

from tests.source_scan import code_only


def _block():
    """The reporting tail of `_execute_live`, comments stripped.

    Stripped because the replacement comments say `EXECUTED_LIVE` and
    `TRADE_ACCEPTED_LIVE` while explaining why they are now conditional, and
    a comment quoting the string it forbids is indistinguishable from the
    code doing it.
    """
    code = code_only(io.open("bot/core/engine.py", encoding="utf-8").read())
    i = code.index("live_failed = execution_indicates_failure(result)")
    j = code.index("def reject_trade", i)
    return code[i:j]


class TestTheChain:
    def test_the_sealed_outcome_depends_on_whether_it_filled(self):
        assert '"EXECUTED_LIVE" if not live_failed else "EXECUTION_FAILED"' in _block()

    def test_execute_live_is_no_longer_asserted_unconditionally(self):
        # The old shape: a bare `outcome="EXECUTED_LIVE",` with nothing
        # conditional about it.
        assert 'outcome="EXECUTED_LIVE",' not in _block()

    def test_a_failed_trade_is_still_sealed_rather_than_dropped(self):
        # Skipping the seal would be the opposite lie: silence about a live
        # entry that was approved, attempted and refused.
        block = _block()
        assert block.count("self.audit_chain.seal_decision(") == 1
        i = block.index("self.audit_chain.seal_decision(")
        # ...and it sits at the OUTER indent, outside `if not live_failed:`,
        # which is correct for a call that now reports both outcomes.
        line_start = block.rindex("\n", 0, i) + 1
        assert i - line_start == 8, "seal_decision moved out of the outer block"

    def test_the_failure_outcome_is_not_named_a_rejection(self):
        # guardian/flight_recorder files any outcome starting with REJECTED as
        # a RISK-GATE rejection. The risk gate approved this one; the venue
        # refused it. Naming the wrong cause is the defect being fixed here,
        # in a new place.
        assert "REJECTED" not in _block().split("outcome=")[1][:120]


class TestTheLearner:
    def test_the_logged_decision_depends_on_whether_it_filled(self):
        assert ('decision="TRADE_ACCEPTED_LIVE" if not live_failed '
                'else "EXECUTION_FAILED"') in _block()

    def test_the_reason_travels_with_it(self):
        assert "rejected_reason=_fail_reason" in _block()

    def test_risk_engine_result_stays_approved_on_both_paths(self):
        # APPROVED is TRUE either way — the risk engine did approve this
        # trade. Flipping it would trade one false claim for another.
        assert 'risk_engine_result="APPROVED",' in _block()


class TestTheTransition:
    def test_the_stated_reason_depends_on_whether_it_filled(self):
        assert '"live trade executed" if not live_failed' in _block()
        assert '"live execution failed"' in _block()


class TestTheNarrative:
    """`explain_fill` turns a sealed outcome into a sentence."""

    def test_a_failed_execution_reads_as_one(self):
        from bot.guardian.explain_fill import explain
        out = explain({"symbol": "TRUMP/USDT", "outcome": "EXECUTION_FAILED",
                            "is_paper": False, "idea": {"direction": "SHORT"}})
        assert "tried and failed to open" in out["headline"]
        assert "evaluated" not in out["headline"]

    def test_a_real_execution_still_reads_as_taken(self):
        from bot.guardian.explain_fill import explain
        out = explain({"symbol": "TRUMP/USDT", "outcome": "EXECUTED_LIVE",
                            "is_paper": False, "idea": {"direction": "SHORT"}})
        assert "took" in out["headline"]

    def test_the_two_sealed_values_were_BOTH_falling_through_before(self):
        # A pre-existing bug the same edit closed: the verb map keyed on
        # "taken"/"confirmed"/"rejected"/"skipped", and the values actually
        # sealed are EXECUTED_LIVE and REJECTED_ON_RECHECK — neither was in
        # it, so every real record rendered "The agent evaluated ...".
        from bot.guardian.explain_fill import explain
        for outcome in ("EXECUTED_LIVE", "REJECTED_ON_RECHECK"):
            out = explain({"symbol": "X/USDT", "outcome": outcome,
                                "is_paper": False, "idea": {"direction": "LONG"}})
            assert "evaluated" not in out["headline"], outcome
