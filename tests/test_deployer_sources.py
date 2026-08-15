"""The deployer source, and the five ways it could have lied.

``deployer_history`` scored provenance for as long as it existed and was fed by
nothing — ``token_research`` passed ``deployer_report=None``, so the section read
**not read** on every dossier ever produced. This is the source that feeds it,
and the tests are about the readings it must REFUSE to invent, because every one
of them would push the verdict in the damning direction:

* an unreadable transaction list becoming ``wallet_age_days: 0`` — a wallet
  under seven days old is a FLAG, so a failed read would manufacture evidence;
* a truncated history becoming a ``prior_deployments`` total, when it is a floor;
* a rate-limited request becoming an empty history;
* a supply share emitted as ``60`` for 60% against a hard threshold of ``0.5``;
* and the big one: ``prior_rugged: 0`` to unlock a ``clean`` verdict, when the
  truth is that no explorer call can determine how a contract ended.
"""
from __future__ import annotations

import asyncio

import pytest

from bot.core.deployer_history import assess_deployer
from bot.core.deployer_sources import EtherscanDeployerSource

NOW = 1_700_000_000.0
DEPLOYER = "0xdead00000000000000000000000000000000beef"
TOKEN = "0xtoken0000000000000000000000000000000000"
CREATION_HASH = "0xcreate"


def _ok(result):
    return {"status": "1", "message": "OK", "result": result}


def _tx(ts, to="0xsomewhere", hash_="0xother"):
    return {"timeStamp": str(int(ts)), "to": to, "hash": hash_}


def _creation_tx(ts, hash_):
    return _tx(ts, to="", hash_=hash_)


class FakeExplorer:
    """Routes by the `action=` in the URL; each action independently faulty."""

    def __init__(self, **actions):
        self.actions = actions
        self.calls = []

    async def __call__(self, url):
        self.calls.append(url)
        for action, resp in self.actions.items():
            if f"action={action}" in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return {"status": "0", "message": "NOTOK", "result": None}


def _source(explorer, now=NOW):
    return EtherscanDeployerSource(api_key="k", transport=explorer, now=lambda: now)


def _fetch(source, chain="eth", address=TOKEN):
    return asyncio.run(source.fetch(chain, address))


def _full_explorer(**over):
    base = {
        "getcontractcreation": _ok([{"contractAddress": TOKEN,
                                     "contractCreator": DEPLOYER,
                                     "txHash": CREATION_HASH}]),
        "getsourcecode": _ok([{"SourceCode": "contract C {}"}]),
        "txlist": _ok([
            _tx(NOW - 400 * 86400),                                # first tx
            _creation_tx(NOW - 300 * 86400, "0xold1"),             # prior
            _creation_tx(NOW - 200 * 86400, "0xold2"),             # prior
            _creation_tx(NOW - 3600, CREATION_HASH),               # THIS token
        ]),
        "tokenbalance": _ok("30"),
        "tokensupply": _ok("1000"),
    }
    base.update(over)
    return FakeExplorer(**base)


# ── configuration ─────────────────────────────────────────────────────────

def test_no_key_is_never_asked_rather_than_asked_and_clean():
    assert EtherscanDeployerSource(api_key="").available() is False
    assert EtherscanDeployerSource(api_key="k").available() is True


def test_an_unsupported_chain_is_an_error_not_an_empty_reading():
    with pytest.raises(RuntimeError):
        _fetch(_source(_full_explorer()), chain="solana")


# ── the deployer address gates everything ─────────────────────────────────

def test_an_unreadable_deployer_returns_nothing_at_all():
    """Every other fact is a fact ABOUT the deployer.

    Returning `{contract_verified: True}` with no deployer would be a
    provenance report about nobody, credited to a source that never
    identified the subject.
    """
    e = _full_explorer(getcontractcreation=_ok([]))
    assert _fetch(_source(e)) == {}


# ── the readings it must refuse to invent ─────────────────────────────────

def test_an_unreadable_history_is_not_a_brand_new_wallet():
    """`wallet_age_days: 0` is a FLAG. A failed read must not produce one."""
    e = _full_explorer(txlist=RuntimeError("rate limited"))
    got = _fetch(_source(e))
    assert "wallet_age_days" not in got, "a failed read manufactured a young wallet"
    assert "prior_deployments" not in got
    # …and the sub-reads that DID work still contribute. One dead sub-read must
    # not blank the rest (CLAUDE.md's composite case).
    assert got["contract_verified"] is True
    assert got["deployer_supply_pct"] == pytest.approx(0.03)


def test_an_empty_history_for_a_proven_deployer_is_a_gap_not_an_age():
    # This address demonstrably deployed a contract, so "no transactions" is a
    # visibility failure, not a wallet that has never transacted.
    e = _full_explorer(txlist=_ok([]))
    got = _fetch(_source(e))
    assert "wallet_age_days" not in got
    assert "prior_deployments" not in got


def test_a_truncated_history_yields_no_deployment_count():
    """At the cap the list is a prefix, so the count is a floor.

    `prior_deployments` is the DENOMINATOR of the deployer's record. A
    denominator quietly too small is a partial total printed as a whole one.
    """
    txs = [_tx(NOW - 500 * 86400)] + [
        _creation_tx(NOW - i, f"0x{i}") for i in range(10_000)]
    e = _full_explorer(txlist=_ok(txs[:10_000]))
    got = _fetch(_source(e))
    assert "prior_deployments" not in got
    assert got["deployments_truncated"] is True
    # The age still reads — it comes from the FIRST record, which truncation
    # at the tail does not touch.
    assert got["wallet_age_days"] == pytest.approx(500, abs=1)


def test_a_throttled_request_is_not_an_empty_history():
    """Etherscan answers `status: 0` for both "no records" and "rate limited"."""
    e = _full_explorer(txlist={"status": "0", "message": "NOTOK: rate limit",
                               "result": None})
    got = _fetch(_source(e))
    assert "prior_deployments" not in got, "a throttle became a clean history"


def test_no_transactions_found_is_a_real_reading():
    # The one `status: 0` that IS an answer.
    e = _full_explorer(txlist={"status": "0", "message": "No transactions found",
                               "result": []})
    got = _fetch(_source(e))
    assert "wallet_age_days" not in got  # empty list, per the rule above
    assert "deployer_address" in got     # but the source did not error out


# ── the readings it gets right ────────────────────────────────────────────

def test_the_counts_and_the_age_read_correctly():
    got = _fetch(_source(_full_explorer()))
    assert got["deployer_address"] == DEPLOYER
    assert got["wallet_age_days"] == pytest.approx(400, abs=1)
    assert got["prior_deployments"] == 2, "this token's own creation is not prior"
    assert got["concurrent_launches_24h"] == 1
    assert got["contract_verified"] is True


def test_an_unverified_contract_reads_false_and_an_unreadable_one_is_absent():
    assert _fetch(_source(_full_explorer(
        getsourcecode=_ok([{"SourceCode": ""}]))))["contract_verified"] is False
    got = _fetch(_source(_full_explorer(getsourcecode=RuntimeError("boom"))))
    assert "contract_verified" not in got, (
        "an unreadable verification status is not an unverified contract")


def test_supply_share_is_a_fraction_not_a_percent():
    """The units trap: the field is named `_pct` and read against 0.5."""
    got = _fetch(_source(_full_explorer(tokenbalance=_ok("600"),
                                        tokensupply=_ok("1000"))))
    assert got["deployer_supply_pct"] == pytest.approx(0.6)
    # And the scorer must agree that this is the ≥50% hard case.
    rep = assess_deployer(got)
    assert any(c["name"] == "deployer_supply_pct" and c["status"] == "hard"
               for c in rep["checks"]), rep["checks"]


def test_a_zero_supply_omits_the_share_rather_than_dividing():
    got = _fetch(_source(_full_explorer(tokensupply=_ok("0"))))
    assert "deployer_supply_pct" not in got


# ── the integration property that matters most ────────────────────────────

def test_this_source_alone_can_never_produce_a_clean_verdict():
    """No explorer call determines how a previous contract ENDED.

    `prior_rugged` is therefore absent, and `_outcomes_resolved` treats that as
    fatal. The verdict is `unproven` WITH content — which is the honest ceiling
    for this source, and the reason it does not fill the field with 0.
    """
    got = _fetch(_source(_full_explorer()))
    assert "prior_rugged" not in got
    assert "prior_alive" not in got
    rep = assess_deployer(got)
    assert rep["verdict"] == "unproven", (
        "a source that cannot see outcomes must not certify a record")
    # …and it is unproven with real content, not the empty unproven that a
    # missing source produces.
    assert rep["outcomes"]["total"] == 2
    assert rep["outcomes"]["rugged"] is None
    assert rep["outcomes"]["unresolved"] == 2
    assert rep["evidence"] >= 4
