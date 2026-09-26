"""Five more stores that read an unreadable file as empty.

The six money stores in `test_an_unreadable_store_is_not_an_empty_one.py` were
driven first. The same shape was in four more, found by the ratchet in
`test_no_json_store_writes_over_a_failed_read.py`, and each was driven before
it was changed:

* THE SECRETS VAULT. `secrets_vault.py`'s own docstring says a file that fails
  to parse deserves the `_load_failed` treatment `exchange_credentials._load`
  got, and `_load_vault` answered ``({}, {})`` for exactly that file. A vault
  holding the exchange keys, truncated, then ONE boot with a single managed
  value in the environment: the file held that value alone. Every exchange
  key, the gateway secret and the signer key -- the file has no ``.bak``.
* THE FREE-CHAT QUOTA. Every question read the counts, a failed read was
  ``{}``, and the save wrote one user's count over every other user's: one
  failed read gave every free user a fresh day.
* THE LEARNING RECORD. A corrupt scorecard file lost every other strategy's
  scorecard to the next evaluation, the prompt registry every other version,
  the proposal backlog every other proposal -- and the orchestrator rewrote
  the backlog WHOLESALE from `get_proposals`, which drops a row it cannot
  validate, so a malformed row was erased by the next status change.
* THE ANCHOR RECORD. `confirm_anchor` wrote this chain's anchor over a file
  it could not read (with `write_text`, not atomically), erasing every other
  chain's anchor, and the /anchor card said "none" for a file that is there.
* THE PUBLISHED STATEMENT. Nothing was erased here -- a publication is sealed
  whole and written without a read -- but `PublicationStore.read` answered
  None for a file that would not parse, and both public readers turned None
  into a claim: /proof said no statement "has been published yet", and the
  agent directory answered a 404 `unknown_agent` the relay caches.

Each keeps the file byte for byte now. What each READER answers is stated
beside it, because it is a different decision per store.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

CORRUPT = b'{"BITGET_API_KEY": "gAAAAAB-truncated'


def _bytes(p: Path) -> bytes:
    return p.read_bytes()


# ── the secrets vault ─────────────────────────────────────────────────────

import bot.core.secrets_vault as sv  # noqa: E402

VAULT = "secrets_vault.enc"


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNECLAW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("SECRETS_VAULT_ENABLED", "true")
    monkeypatch.delenv("RUNECLAW_SECRETS_KEY", raising=False)
    for k in sv._managed_keys():
        monkeypatch.delenv(k, raising=False)


def _seed(monkeypatch, tmp_path, **secrets):
    for k, v in secrets.items():
        monkeypatch.setenv(k, v)
    sv.seed_and_restore()
    for k in secrets:
        monkeypatch.delenv(k, raising=False)
    return tmp_path / VAULT


class TestTheVault:
    def test_a_boot_does_not_write_over_a_vault_that_will_not_read(
            self, tmp_path, monkeypatch):
        """The driven erasure: a truncated vault, then one boot with ONE
        managed value in the environment (enough to make the boot save)."""
        _isolate(monkeypatch, tmp_path)
        vault = _seed(monkeypatch, tmp_path,
                      BITGET_API_KEY="AKEY", BITGET_API_SECRET="ASEC")
        vault.write_bytes(CORRUPT)

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
        summary = sv.seed_and_restore()

        assert _bytes(vault) == CORRUPT, (
            "a boot wrote the environment's one value over a vault it could "
            "not read -- every other secret in it is gone, and it has no .bak")
        assert summary.get("vault") == "unreadable", (
            "the boot summary must say the vault did not read, not that "
            "nothing needed restoring")
        assert summary.get("restored") in (None, [], ()), (
            "nothing can have been restored from a file that did not read")

    def test_a_vault_that_is_not_a_map_is_not_an_empty_one(
            self, tmp_path, monkeypatch):
        """Parses, and is not a map: the old loader's `isinstance` check turned
        it into ``{}`` and the next save replaced it."""
        _isolate(monkeypatch, tmp_path)
        vault = tmp_path / VAULT
        vault.write_text(json.dumps(["BITGET_API_KEY", "gAAAA"]))
        before = _bytes(vault)
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
        assert sv.seed_and_restore().get("vault") == "unreadable"
        assert _bytes(vault) == before

    def test_setting_a_secret_does_not_write_over_it(self, tmp_path,
                                                     monkeypatch):
        """/setgateway is what an operator runs when a secret has gone
        missing: the recovery path must not be the erasure path. The value
        still reaches THIS process."""
        _isolate(monkeypatch, tmp_path)
        vault = _seed(monkeypatch, tmp_path, BITGET_API_KEY="AKEY")
        vault.write_bytes(CORRUPT)

        sv.store_secrets({"WEB_GATEWAY_SECRET": "g" * 48})

        assert _bytes(vault) == CORRUPT
        assert sv.os.environ.get("WEB_GATEWAY_SECRET") == "g" * 48

    def test_a_readable_vault_is_still_written(self, tmp_path, monkeypatch):
        """The refusal is for a file that will not read, not for every file:
        the ordinary store still lands and still decrypts."""
        _isolate(monkeypatch, tmp_path)
        _seed(monkeypatch, tmp_path, BITGET_API_KEY="AKEY")
        sv.store_secrets({"WEB_GATEWAY_SECRET": "g" * 48})
        raw = json.loads((tmp_path / VAULT).read_text())
        assert {"BITGET_API_KEY", "WEB_GATEWAY_SECRET"} <= set(raw)

    def test_the_file_state_names_an_unreadable_vault(self, tmp_path,
                                                      monkeypatch):
        """`vault_status` answers ``{}`` for a vault that is switched off and
        for one that will not read; the file state is what tells them apart.
        It names the exception's class and nothing inside the file."""
        _isolate(monkeypatch, tmp_path)
        vault = _seed(monkeypatch, tmp_path, BITGET_API_KEY="AKEY")
        assert sv.vault_file_state() == ("read", "")
        vault.write_bytes(CORRUPT)
        assert sv.vault_status() == {}
        state, detail = sv.vault_file_state()
        assert state == "unreadable"
        assert detail == "JSONDecodeError"
        assert "gAAAA" not in detail
        vault.unlink()
        assert sv.vault_file_state()[0] == "fresh"

    @pytest.mark.asyncio
    async def test_the_card_says_the_file_did_not_read_not_disabled(
            self, tmp_path, monkeypatch):
        """The /vault card printed "disabled or crypto missing" for a vault
        file that would not read: a named cause, for the one of the two it
        is not."""
        from bot.skills.account_commands import AccountCommands

        _isolate(monkeypatch, tmp_path)
        vault = _seed(monkeypatch, tmp_path, BITGET_API_KEY="AKEY")
        vault.write_bytes(CORRUPT)

        class _Card(AccountCommands):
            def __init__(self):
                self.sent: list[str] = []

            async def _send(self, update, text, reply_markup=None, edit=False):
                self.sent.append(text)

            def _is_admin(self, update) -> bool:
                return True

        h = _Card()
        await h._cmd_vault(object(), None)
        out = "\n".join(h.sent)
        assert "could not be read" in out
        assert "JSONDecodeError" in out
        assert "disabled" not in out
        assert "nothing will be written over it" in out
        assert "gAAAA" not in out and "AKEY" not in out

    def test_a_master_key_that_does_not_match_is_still_the_other_case(
            self, tmp_path, monkeypatch):
        """The per-ENTRY case (a key change: the file parses, entries do not
        decrypt) keeps its own treatment -- ciphertext carried through, the
        vault stays online. Refusing every save there would take the vault
        offline over one stale key."""
        _isolate(monkeypatch, tmp_path)
        vault = _seed(monkeypatch, tmp_path, BITGET_API_KEY="AKEY")
        before = json.loads(vault.read_text())
        (tmp_path / ".exchange_secret.key").write_bytes(Fernet.generate_key())
        sv.store_secrets({"WEB_GATEWAY_SECRET": "g" * 48})
        after = json.loads(vault.read_text())
        assert after["BITGET_API_KEY"] == before["BITGET_API_KEY"]
        assert "WEB_GATEWAY_SECRET" in after


# ── the free-chat quota ───────────────────────────────────────────────────

from bot.web import chat_quota  # noqa: E402


@pytest.fixture
def quota(monkeypatch, tmp_path):
    p = tmp_path / "quota.json"
    monkeypatch.setattr(chat_quota, "_STORE_PATH", p)
    monkeypatch.delenv("FREE_CHAT_DAILY_LIMIT", raising=False)
    monkeypatch.setenv("FREE_CHAT_QUOTA_ENABLED", "1")
    return p


class TestTheFreeChatQuota:
    def test_a_question_does_not_write_over_an_unreadable_file(self, quota):
        """Every other free user's count is in that file. Folding the failed
        read into ``{}`` and saving one user's row over it gave every free
        user a fresh day."""
        chat_quota.consume("web:a", "basic")
        chat_quota.consume("web:b", "basic")
        quota.write_bytes(CORRUPT)

        q = chat_quota.consume("web:c", "basic")

        assert _bytes(quota) == CORRUPT
        assert q["allowed"] is True
        assert q["unmetered"] is True
        assert q["unread"] == "JSONDecodeError"
        assert q["used"] is None and q["remaining"] is None, (
            "a count nobody read is not a count of zero")

    def test_the_allowance_is_the_soft_limits_choice_not_a_refusal(
            self, quota):
        """This is a soft spend fence, not a security control: refusing every
        free user's chat over a file fault would make it an outage. The
        question is allowed and NOT counted -- `unmetered()`'s answer."""
        quota.write_bytes(CORRUPT)
        for _ in range(chat_quota.free_daily_limit() + 3):
            assert chat_quota.consume("web:a", "basic")["allowed"] is True
        assert _bytes(quota) == CORRUPT

    def test_a_refund_writes_nothing_over_it(self, quota):
        quota.write_bytes(CORRUPT)
        chat_quota.refund("web:a", "basic")
        assert _bytes(quota) == CORRUPT

    def test_the_status_says_it_could_not_read(self, quota):
        quota.write_bytes(CORRUPT)
        st = chat_quota.status("web:a", "basic")
        assert st["unread"] == "JSONDecodeError"
        assert st["used"] is None
        assert "allowed" not in st

    def test_a_missing_file_is_a_fresh_day_and_an_empty_one_too(self, quota):
        assert chat_quota.consume("web:a", "basic")["used"] == 1
        quota.write_text("")
        assert chat_quota.consume("web:a", "basic")["used"] == 1
        assert json.loads(quota.read_text())["web:a"]["n"] == 1


# ── the learning record ───────────────────────────────────────────────────

from bot.learning.models import ImprovementProposal, PromptVersion, StrategyScorecard  # noqa: E402
from bot.learning.orchestrator import LearningOrchestrator  # noqa: E402
from bot.learning.store import LearningStore  # noqa: E402


class TestTheLearningRecord:
    def test_an_evaluation_does_not_write_over_an_unreadable_scorecard(
            self, tmp_path):
        s = LearningStore(str(tmp_path))
        s.update_scorecard(StrategyScorecard(strategy_name="alpha"))
        f = tmp_path / "strategy_scorecard.json"
        f.write_bytes(CORRUPT)
        s.update_scorecard(StrategyScorecard(strategy_name="beta"))
        assert _bytes(f) == CORRUPT
        # The reader answers "none" with an error logged -- a stated choice:
        # these rank strategies, nothing here decides money.
        assert s.get_scorecards() == {}

    def test_a_prompt_version_does_not_write_over_the_registry(self, tmp_path):
        s = LearningStore(str(tmp_path))
        f = tmp_path / "prompt_versions.json"
        f.write_bytes(CORRUPT)
        s.record_prompt_version(PromptVersion(version_id="v2"))
        assert _bytes(f) == CORRUPT

    def test_a_proposal_does_not_write_over_the_backlog(self, tmp_path):
        s = LearningStore(str(tmp_path))
        f = tmp_path / "improvement_backlog.json"
        f.write_bytes(CORRUPT)
        s.record_proposal(ImprovementProposal(problem="p", proposed_change="c"))
        assert _bytes(f) == CORRUPT

    def test_triage_keeps_a_row_it_could_not_validate(self, tmp_path):
        """The orchestrator re-read the backlog through `get_proposals`, which
        DROPS a row it cannot validate, and wrote that list back whole."""
        orch = LearningOrchestrator(data_dir=str(tmp_path))
        orch.store.record_proposal(
            ImprovementProposal(problem="p", proposed_change="c"))
        f = tmp_path / "improvement_backlog.json"
        rows = json.loads(f.read_text())
        # A timestamp pydantic cannot parse: `get_proposals` drops this row.
        bad = {"audit_id": "hand-edited", "timestamp_utc": "not a date",
               "status": "pending"}
        # And a row that is not a map at all: the status writer walks every
        # row, and one it cannot ask for an `audit_id` must be stepped over,
        # not take the whole change down with it.
        note = "a note somebody left in the backlog"
        rows.extend([note, bad])
        f.write_text(json.dumps(rows))

        orch.process_proposals()

        after = json.loads(f.read_text())
        assert bad in after, "a row the reader could not validate was erased"
        assert note in after, "a row that is not a map was erased"
        assert after[0]["status"] != "pending", "the triage did not land"

    def test_triage_does_not_write_over_an_unreadable_backlog(self, tmp_path):
        orch = LearningOrchestrator(data_dir=str(tmp_path))
        f = tmp_path / "improvement_backlog.json"
        f.write_bytes(CORRUPT)
        orch.process_proposals()
        assert _bytes(f) == CORRUPT

    def test_an_unchanged_status_writes_nothing(self, tmp_path):
        s = LearningStore(str(tmp_path))
        p = ImprovementProposal(problem="p", proposed_change="c")
        s.record_proposal(p)
        f = tmp_path / "improvement_backlog.json"
        before = _bytes(f)
        f.touch()
        mtime = f.stat().st_mtime_ns
        s.set_proposal_statuses({str(p.audit_id): "pending"})
        assert _bytes(f) == before
        assert f.stat().st_mtime_ns == mtime


# ── the anchor record ─────────────────────────────────────────────────────

from bot.proofofpnl import anchor  # noqa: E402

_AGENT = "0x" + "ab" * 20
_PUBKEY = "cd" * 32
_TX = "0x" + "11" * 32


@pytest.fixture
def anchor_state(tmp_path, monkeypatch):
    p = tmp_path / "anchor_state.json"
    monkeypatch.setenv("ANCHOR_STATE_PATH", str(p))
    monkeypatch.delenv("ANCHOR_REGISTRY_ADDRESS", raising=False)
    monkeypatch.delenv("ANCHOR_CHAIN_ID", raising=False)
    c = anchor.identity_commitment(_AGENT, _PUBKEY)
    tx = {"from": _AGENT, "to": _AGENT, "input": anchor.anchor_calldata(c)}
    rcpt = {"status": "0x1", "blockNumber": "0x1a2b3c"}

    def rpc(method, params):
        return {"eth_getTransactionByHash": tx,
                "eth_getTransactionReceipt": rcpt}[method]

    monkeypatch.setattr(anchor, "_rpc", rpc)
    return p


class TestTheAnchorRecord:
    def test_a_confirm_does_not_write_over_an_unreadable_record(
            self, anchor_state):
        anchor_state.write_bytes(CORRUPT)
        ok, problems = anchor.confirm_anchor(_TX, _AGENT, _PUBKEY)
        assert _bytes(anchor_state) == CORRUPT, (
            "every other chain's anchor was in that file")
        assert ok is False, "an anchor that was not recorded is not recorded"
        assert any("could not be read" in p and "NOT recorded" in p
                   for p in problems)

    def test_a_confirm_keeps_the_other_chains(self, anchor_state):
        anchor_state.write_text(json.dumps({"1": {"tx_hash": "0xmainnet"}}))
        ok, _ = anchor.confirm_anchor(_TX, _AGENT, _PUBKEY)
        assert ok
        after = json.loads(anchor_state.read_text())
        assert after["1"] == {"tx_hash": "0xmainnet"}
        assert str(anchor.BASE_CHAIN_ID) in after

    def test_an_unreadable_record_publishes_no_verified_anchor(
            self, anchor_state):
        """VERIFIED is a public claim; an unreadable record makes none -- the
        card's own UNVERIFIED plan passes through, as it does for no record."""
        plan = {"status": "UNVERIFIED", "note": "plan"}
        assert anchor.confirm_anchor(_TX, _AGENT, _PUBKEY)[0]
        assert anchor.anchor_for_card(_AGENT, _PUBKEY, "h" * 64,
                                      plan)["status"] == "VERIFIED"
        anchor_state.write_bytes(CORRUPT)
        assert anchor.anchor_for_card(_AGENT, _PUBKEY, "h" * 64, plan) is plan

    def test_the_reader_says_unreadable_not_none(self, anchor_state):
        from bot.utils.json_store import StoreUnreadable
        assert anchor.read_anchor_state() == {}
        anchor_state.write_bytes(CORRUPT)
        with pytest.raises(StoreUnreadable):
            anchor.read_anchor_state()


@pytest.mark.asyncio
async def test_the_anchor_card_says_the_record_did_not_read(
        anchor_state, monkeypatch):
    """The /anchor card printed ``Recorded anchors: none`` for a record file
    that is there and will not read: a confident negative, one line above
    the transaction the operator is told to send."""
    import bot.utils.attestation as att
    from bot.skills.telegram_handler import TelegramHandler

    class _Engine:
        available = True
        public_key_hex = _PUBKEY

    monkeypatch.setattr(att, "AttestationEngine", _Engine)
    monkeypatch.setenv("PROOFOFPNL_AGENT_ADDRESS", _AGENT)

    def _offline(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr(anchor, "_rpc", _offline)

    h = TelegramHandler.__new__(TelegramHandler)
    sent: list[str] = []

    async def _send(update, text, reply_markup=None, edit=False):
        sent.append(text)

    async def _guard(update, command, ctx=None):
        return True

    h._send = _send
    h._is_admin = lambda update: True
    h._guard = _guard

    class _Ctx:
        args: list = []

    anchor_state.write_bytes(CORRUPT)
    await h._cmd_anchor(object(), _Ctx())
    out = "\n".join(sent)
    line = next(ln for ln in out.splitlines() if "Recorded anchors" in ln)
    assert "could not be read" in line and "JSONDecodeError" in line
    assert "none" not in line
    assert _bytes(anchor_state) == CORRUPT

    anchor_state.unlink()
    sent.clear()
    await h._cmd_anchor(object(), _Ctx())
    line = next(ln for ln in "\n".join(sent).splitlines()
                if "Recorded anchors" in ln)
    assert "none" in line


# ── the published Proof-of-PnL statement ──────────────────────────────────

import bot.proofofpnl.publish as publish_mod  # noqa: E402


@pytest.fixture
def pub_store(tmp_path, monkeypatch):
    store = publish_mod.PublicationStore(str(tmp_path / "pub.json"))
    monkeypatch.setattr(publish_mod, "_STORE", store)
    return store, tmp_path / "pub.json"


class TestThePublishedStatement:
    def test_the_reader_says_unreadable_not_unpublished(self, pub_store):
        from bot.utils.json_store import StoreUnreadable
        store, path = pub_store
        assert store.read() is None                  # never written: a reading
        path.write_bytes(b"")
        assert store.read() is None                  # an empty file is fresh
        path.write_bytes(CORRUPT)
        with pytest.raises(StoreUnreadable):
            store.read()
        path.write_text("[1, 2]")                    # parses, is not a statement
        with pytest.raises(StoreUnreadable):
            store.read()

    def test_proof_says_unavailable_not_never_published(self, pub_store):
        from bot.web import user_gateway as ug
        _, path = pub_store
        path.write_bytes(CORRUPT)
        body = ug._proofofpnl_payload()
        assert body == {"published": False, "error": "unavailable"}
        assert "note" not in body, (
            "'No Proof-of-PnL statement has been published yet' is a claim "
            "about a file nobody could read")
        path.unlink()
        body = ug._proofofpnl_payload()
        assert body["published"] is False
        assert "published yet" in body["note"]

    @pytest.mark.asyncio
    async def test_the_agent_card_is_unavailable_not_unknown(self, pub_store):
        """A 404 ``unknown_agent`` is cached by the relay and called a
        measured absence by the website; nobody measured it here."""
        from aiohttp.test_utils import make_mocked_request

        from bot.web.user_gateway import handle_agent_card_public
        _, path = pub_store
        addr = "0x" + "ab" * 20

        async def _get():
            req = make_mocked_request("GET", f"/gateway/public/agent/{addr}",
                                      match_info={"address": addr})
            return await handle_agent_card_public(req)

        path.write_bytes(CORRUPT)
        resp = await _get()
        assert resp.status == 503
        assert json.loads(resp.body) == {"error": "unavailable"}
        assert _bytes(path) == CORRUPT

        path.unlink()
        resp = await _get()
        assert resp.status == 404, "a store with no publication IS a reading"
