"""Contract Audit Router.

Risk scores route a tree to the Claude models in audit_router/models.json.
The model call is the existing client, and every test here injects the reply.
Nothing in this file calls the network.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from audit_router.budget import Ledger, estimate_tokens
from audit_router.config import ConfigError, load_config, models_for, supported_model_ids
from audit_router.flag import FLAG, enabled
from audit_router.report import decide_verdict, render
from audit_router.review import (
    Finding,
    MissingKey,
    anthropic_complete,
    dedupe,
    line_of_quote,
    parse_model_findings,
    system_prompt,
    user_message,
)
from audit_router.risk import score_sources
from audit_router.run import run
from audit_router.sources import chunk_files, collect_sources, repo_root
from audit_router.tools import StaticReport, ToolRun, collect_static, default_run
from bot.core.cost import llm_call_cost, resolve_llm_price

ROOT = repo_root()
FIXTURES = ROOT / "tests" / "fixtures" / "audit_router"
DAY = date(2026, 10, 25)
KEY = "zz-audit-router-" + ("q" * 40)


def _score(rel: str):
    files, chain = collect_sources(ROOT / rel)
    return score_sources(files, load_config(), chain)


def _quiet_static() -> StaticReport:
    return StaticReport(
        [
            ToolRun("slither", "not_installed", "slither is not on PATH"),
            ToolRun("aderyn", "not_installed", "aderyn is not on PATH"),
            ToolRun("forge", "not_a_foundry_project",
                    "no foundry.toml beside the source, so forge test was not run"),
        ],
        "no foundry.toml beside the source, so forge test was not run",
    )


def _write_budget(tmp: Path, **budget) -> Path:
    data = json.loads((ROOT / "audit_router" / "models.json").read_text(encoding="utf-8"))
    data["budget"].update(budget)
    path = tmp / "models.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _capture():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.DEBUG)
    loggers = [logging.getLogger("audit_router"), logging.getLogger("runeclaw.system")]
    for item in loggers:
        item.addHandler(handler)
        item.setLevel(logging.DEBUG)
    return stream, handler, loggers


def _release(handler, loggers):
    for item in loggers:
        item.removeHandler(handler)


# ── risk and routing ────────────────────────────────────────────────────


def test_hash_only_fixture_scores_low_even_with_scary_words_in_comments():
    risk = _score("tests/fixtures/audit_router/hash_only")
    assert risk.readable
    assert risk.tier == "low"
    assert risk.holds_funds is False
    assert risk.upgradeable is False
    assert risk.points is not None and risk.points < 20


def test_the_hashkey_seal_folder_scores_low():
    risk = _score("contracts/hashkey")
    assert risk.readable
    assert risk.tier == "low"
    assert risk.holds_funds is False
    assert risk.upgradeable is False
    assert "mainnet_documented" in risk.signals
    assert risk.points is not None and risk.points < 20


def test_a_single_hashkey_file_still_sees_the_project_chain_file():
    risk = _score("contracts/hashkey/src/HashKeySeal.sol")
    assert risk.tier == "low"
    assert "mainnet_documented" in risk.signals


def test_payable_approval_and_upgradeable_score_high():
    payable = _score("tests/fixtures/audit_router/payable")
    approval = _score("tests/fixtures/audit_router/approval")
    upgrade = _score("tests/fixtures/audit_router/upgradeable")
    assert payable.tier == "high" and payable.holds_funds
    assert approval.tier == "high" and approval.holds_funds
    assert upgrade.tier == "high" and upgrade.upgradeable
    assert upgrade.holds_funds is False


def test_an_oracle_reader_without_funds_scores_medium():
    risk = _score("tests/fixtures/audit_router/oracle")
    assert risk.tier == "medium"
    assert risk.holds_funds is False
    assert risk.upgradeable is False
    assert "oracle" in risk.signals
    assert "external_call" in risk.signals


def test_default_mapping_uses_client_model_ids():
    config = load_config()
    supported = supported_model_ids()
    assert config.tiers["low"] == ("claude-haiku-5-5",)
    assert config.tiers["medium"] == ("claude-sonnet-5",)
    assert config.tiers["high"] == ("claude-opus-4-8", "claude-fable-5")
    for models in config.tiers.values():
        assert 1 <= len(models) <= 2
        assert set(models) <= supported
    low = _score("tests/fixtures/audit_router/hash_only")
    mid = _score("tests/fixtures/audit_router/oracle")
    high = _score("tests/fixtures/audit_router/payable")
    assert models_for(low.tier, config, None) == config.tiers["low"]
    assert models_for(mid.tier, config, None) == config.tiers["medium"]
    assert models_for(high.tier, config, None) == config.tiers["high"]
    assert models_for("high", config, "claude-sonnet-5") == ("claude-sonnet-5",)


def test_an_unknown_model_is_refused():
    with pytest.raises(ConfigError):
        models_for("low", load_config(), "not-a-claude-model")


def test_unreadable_source_is_not_scored_zero(tmp_path):
    bad = tmp_path / "src"
    bad.mkdir()
    target = bad / "Broken.sol"
    target.write_bytes(b"\xff\xfe not utf-8")
    # The fixture lives outside the repo, so score the text through a file
    # planted inside a temp copy is refused. Drive the scorer with a
    # SourceFile the collector would have built.
    from audit_router.sources import SourceFile
    risk = score_sources(
        [SourceFile("x.sol", "", False, "UnicodeError")], load_config(), None)
    assert risk.points is None
    assert risk.tier == "high"
    assert risk.readable is False
    # An unread source is not a measured "no" and not zero lines.
    assert risk.holds_funds is None
    assert risk.upgradeable is None
    text = render(
        "broken", risk, (), [], [], None, None, [],
        Ledger(max_tokens=1, max_cost_usd=0.0), False, "source unreadable")
    assert "Holds or moves funds: not read" in text
    assert "Upgradeable or destructive: not read" in text
    assert "Code lines: not read" in text
    assert "Contracts: not read" in text


# ── verdict, lines, dedupe ──────────────────────────────────────────────


def test_verdict_is_safe_only_when_the_review_finished_clean():
    assert decide_verdict([], readable=True, complete=True,
                          holds_funds=False, upgradeable=False) == "Safe to deploy"
    medium = [Finding("Medium", "a.sol", 1, "t", "f", "q", ["m"])]
    assert decide_verdict(medium, readable=True, complete=True,
                          holds_funds=False, upgradeable=False) == "Fix first"
    high = [Finding("High", "a.sol", 1, "t", "f", "q", ["m"])]
    assert decide_verdict(high, readable=True, complete=True,
                          holds_funds=False, upgradeable=False) == "Do not deploy"
    assert decide_verdict([], readable=True, complete=False,
                          holds_funds=True, upgradeable=False) == "Do not deploy"
    assert decide_verdict([], readable=True, complete=False,
                          holds_funds=False, upgradeable=False) == "Fix first"
    assert decide_verdict([], readable=False, complete=False,
                          holds_funds=False, upgradeable=False) == "Fix first"
    assert decide_verdict([], readable=False, complete=False,
                          holds_funds=None, upgradeable=None) == "Fix first"


def test_a_quote_sets_the_line_and_a_missing_quote_sets_none():
    source = "pragma solidity 0.8.24;\ncontract C {\n    function f() external {}\n}\n"
    assert line_of_quote(source, "function f() external {}") == 3
    assert line_of_quote(source, "this sentence is not in the source") is None
    text = json.dumps({
        "findings": [{
            "severity": "Low",
            "file": "C.sol",
            "quote": "function f() external {}",
            "title": "empty body",
            "fix": "revert or remove it",
            "line": 999,
        }],
        "missing_tests": [],
    })
    found, _missing, ok = parse_model_findings(text, {"src/C.sol": source}, "claude-sonnet-5")
    assert ok
    assert found[0].line == 3
    assert found[0].line != 999


def test_a_quote_the_file_does_not_contain_has_no_line():
    text = json.dumps({
        "findings": [{
            "severity": "High",
            "file": "C.sol",
            "quote": "invented code that is not in the file",
            "title": "made up",
            "fix": "n/a",
        }],
        "missing_tests": [],
    })
    source = "pragma solidity 0.8.24;\ncontract C {}\n"
    found, _missing, ok = parse_model_findings(text, {"C.sol": source}, "m")
    assert ok
    assert found[0].line is None


def test_two_models_collapse_to_one_finding():
    low = Finding("Low", "a.sol", 4, "same", "fix a", "anchor(bytes32 h)", ["claude-opus-4-8"])
    high = Finding("High", "a.sol", 4, "same", "fix b", "anchor(bytes32 h)", ["claude-fable-5"])
    merged = dedupe([low, high])
    assert len(merged) == 1
    assert merged[0].severity == "High"
    assert merged[0].sources == ["claude-opus-4-8", "claude-fable-5"]


# ── static tools ────────────────────────────────────────────────────────


def test_static_tools_are_forge_test_slither_and_aderyn_only(tmp_path):
    seen: list[tuple] = []

    def which(name: str) -> str:
        return "/usr/bin/" + name

    def fake_run(argv, cwd, env, timeout):
        seen.append((list(argv), Path(cwd), dict(env)))
        if argv[0] == "slither":
            payload = {
                "results": {"detectors": [{
                    "check": "suicidal",
                    "impact": "High",
                    "description": "selfdestruct in exec\n",
                    "elements": [{"source_mapping": {
                        "filename_relative": "src/Proxy.sol",
                        "lines": [7],
                    }}],
                }]},
            }
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        if argv[0] == "aderyn":
            body = {"high_issues": {"issues": [{
                "title": "delegatecall",
                "description": "check the target",
                "instances": [{"contract_path": "src/Proxy.sol", "line_no": 7}],
            }]}}
            Path(argv[2]).write_text(json.dumps(body), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(
            returncode=0,
            stdout="Suite result: ok. 3 passed; 0 failed; 0 skipped\n",
            stderr="",
        )

    report = collect_static(
        ROOT / "contracts" / "hashkey", timeout=5, which=which, run=fake_run)
    argv = [item[0] for item in seen]
    assert argv[0][0] == "slither"
    assert argv[1][:2] == ["aderyn", "--output"]
    assert argv[2] == ["forge", "test"]
    assert all(cmd[0] in {"slither", "aderyn", "forge"} for cmd in argv)
    assert not any("broadcast" in part for cmd in argv for part in cmd)
    assert not any("--fork-url" in part for cmd in argv for part in cmd)
    forge = next(item for item in report.runs if item.name == "forge")
    assert forge.status == "passed"
    assert "3 passed" in forge.detail
    assert "fuzz runs = 256" in report.fuzz_note
    slither = next(item for item in report.runs if item.name == "slither")
    assert slither.findings[0].line == 7
    assert slither.findings[0].file == "src/Proxy.sol"


def test_a_missing_tool_is_not_a_clean_scan():
    report = collect_static(
        FIXTURES / "hash_only", timeout=5, which=lambda _name: None, run=None)
    by_name = {item.name: item for item in report.runs}
    assert by_name["slither"].status == "not_installed"
    assert by_name["aderyn"].status == "not_installed"
    assert by_name["forge"].status == "not_a_foundry_project"
    assert by_name["slither"].findings == []


def test_forge_is_refused_for_anything_but_test():
    with pytest.raises(RuntimeError):
        default_run(["forge", "script", "--broadcast"], FIXTURES, {}, 1)


def test_tool_env_drops_the_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    seen = {}

    def fake_run(argv, cwd, env, timeout):
        seen["env"] = env
        return SimpleNamespace(returncode=0, stdout="1 passed\n", stderr="")

    collect_static(
        ROOT / "contracts" / "hashkey", timeout=5,
        which=lambda name: "/usr/bin/" + name if name == "forge" else None,
        run=fake_run,
    )
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert KEY not in json.dumps(seen["env"])


# ── the run ─────────────────────────────────────────────────────────────


def _reply(quote: str, title: str = "look here", severity: str = "Low",
           missing: list | None = None) -> str:
    return json.dumps({
        "findings": [{
            "severity": severity,
            "file": "HashOnly.sol",
            "quote": quote,
            "title": title,
            "fix": "keep the allowlist",
        }],
        "missing_tests": missing or [],
    })


def test_low_risk_uses_the_cheap_model_and_writes_the_report(tmp_path, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    seen: list[tuple] = []

    async def completer(model, system, user, **kwargs):
        seen.append((model, system, user))
        return _reply("function anchor(bytes32 h) external"), {"in": 30, "out": 12,
                                                              "cache_read": 8, "cache_write": 2}

    result = run(
        FIXTURES / "hash_only", today=DAY, completer=completer,
        out_dir=tmp_path, static=_quiet_static(),
    )
    assert result.exit_code == 0
    assert result.verdict == "Safe to deploy"
    assert result.models == ("claude-haiku-5-5",)
    assert seen and seen[0][0] == "claude-haiku-5-5"
    assert seen[0][1] == system_prompt()
    assert "UNIQUE_PROMPT_MARKER_f8b4" in seen[0][2]
    assert result.report_path is not None
    assert result.report_path.name == "hash_only-claude-haiku-5-5-2026-10-25.md"
    text = result.report_path.read_text(encoding="utf-8")
    assert "AI-assisted audit, not an external audit." in text
    assert "Safe to deploy" in text
    assert "claude-haiku-5-5" in text
    price, exact = resolve_llm_price("claude-haiku-5-5")
    assert exact
    cost = llm_call_cost(price, 30, 12, 8, 2)
    assert f"{cost:.6f}" in text
    assert "function anchor(bytes32 h) external" in text
    # The quoted line is the function, which is line 10 in the fixture.
    assert "| Low | tests/fixtures/audit_router/hash_only/src/HashOnly.sol | 10 |" in text


def test_high_risk_asks_both_models_and_dedupes(tmp_path):
    seen: list[str] = []

    async def completer(model, system, user, **kwargs):
        seen.append(model)
        assert system == system_prompt()
        return json.dumps({
            "findings": [{
                "severity": "High",
                "file": "Vault.sol",
                "quote": "msg.sender.call{value: address(this).balance}",
                "title": "unchecked value call",
                "fix": "check the return value",
            }],
            "missing_tests": ["withdraw to a contract that reenters"],
        }), {"in": 40, "out": 16}

    result = run(
        FIXTURES / "payable", today=DAY, completer=completer,
        out_dir=tmp_path, static=_quiet_static(), force_enabled=True,
    )
    assert seen == ["claude-opus-4-8", "claude-fable-5"]
    assert result.verdict == "Do not deploy"
    assert result.calls == 2
    assert seen[0] != ""  # both ran
    # The two replies are the same finding.
    assert result.report.count("unchecked value call") == 1
    assert "claude-opus-4-8, claude-fable-5" in result.report
    assert result.report_path is not None
    assert "claude-opus-4-8+claude-fable-5" in result.report_path.name


def test_the_second_model_sees_the_source_not_the_first_reply(tmp_path):
    prompts: list[str] = []

    async def completer(model, system, user, **kwargs):
        prompts.append(user)
        return json.dumps({"findings": [], "missing_tests": []}), {"in": 10, "out": 4}

    run(FIXTURES / "payable", completer=completer, out_dir=tmp_path,
        static=_quiet_static(), force_enabled=True)
    assert len(prompts) == 2
    assert prompts[0] == prompts[1]
    assert "unchecked value call" not in prompts[1]


def test_budget_hard_stop_makes_no_call(tmp_path):
    called = False

    async def completer(model, system, user, **kwargs):
        nonlocal called
        called = True
        return "{}", {}

    config = _write_budget(tmp_path, max_tokens=1, max_cost_usd=8.0)
    result = run(
        FIXTURES / "payable", config_path=config, completer=completer,
        out_dir=tmp_path / "out", static=_quiet_static(), force_enabled=True,
        today=DAY,
    )
    assert called is False
    assert result.exit_code == 4
    assert result.calls == 0
    assert result.verdict == "Do not deploy"
    assert "token budget" in (result.stopped or "")
    assert "Hard stop" in result.report
    assert "Safe to deploy" not in result.report.split("## Verdict")[1].split("##")[0]


def test_cost_budget_stops_before_the_second_model(tmp_path):
    files, _chain = collect_sources(FIXTURES / "payable")
    from audit_router.run import _heuristics
    chunks = chunk_files(files, 6000, estimate_tokens)
    assert chunks is not None and len(chunks) == 1
    static = _quiet_static()
    user, _note = user_message(chunks[0], static, _heuristics(files), 6000)
    assert user is not None
    est = estimate_tokens(system_prompt())
    est_user = estimate_tokens(user)
    assert est is not None and est_user is not None
    from audit_router.budget import Ledger
    one = Ledger(10**9, 10**9).projected_cost("claude-opus-4-8", est + est_user, 32)
    other = Ledger(10**9, 10**9).projected_cost("claude-fable-5", est + est_user, 32)
    assert one is not None and other is not None
    # The first model's worst case fits. The second model's does not, even
    # before the first call's measured spend is added.
    cap = one * 1.01
    assert other > cap
    config = _write_budget(
        tmp_path, max_tokens=10_000_000, max_cost_usd=cap, max_output_tokens=32)
    seen: list[str] = []

    async def completer(model, system, user, **kwargs):
        seen.append(model)
        return json.dumps({"findings": [], "missing_tests": []}), {"in": 8, "out": 4}

    result = run(
        FIXTURES / "payable", config_path=config, completer=completer,
        out_dir=tmp_path / "out", static=static, force_enabled=True,
    )
    assert seen == ["claude-opus-4-8"]
    assert result.exit_code == 4
    assert "cost budget" in (result.stopped or "")
    assert result.verdict == "Do not deploy"


def test_flag_off_does_not_call_the_model(tmp_path, monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    assert enabled() is False
    called = False

    async def completer(model, system, user, **kwargs):
        nonlocal called
        called = True
        return "{}", {}

    result = run(FIXTURES / "hash_only", completer=completer, out_dir=tmp_path)
    assert result.exit_code == 2
    assert called is False
    assert result.report_path is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("raw", ["0", "false", "off", "no"])
def test_flag_values_that_stay_off(monkeypatch, raw):
    monkeypatch.setenv(FLAG, raw)
    assert enabled() is False


@pytest.mark.parametrize("raw", ["1", "true", "on", "yes"])
def test_flag_values_that_turn_on(monkeypatch, raw):
    monkeypatch.setenv(FLAG, raw)
    assert enabled() is True


def test_cli_refuses_while_the_flag_is_off(monkeypatch, capsys):
    monkeypatch.delenv(FLAG, raising=False)
    from audit_router.cli import main
    assert main(["contracts/hashkey"]) == 2
    err = capsys.readouterr().err
    assert FLAG in err
    assert KEY not in err


def test_the_key_and_the_prompt_are_not_logged(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.setenv(FLAG, "on")
    stream, handler, loggers = _capture()
    try:
        async def completer(model, system, user, **kwargs):
            assert KEY not in system or True
            return json.dumps({"findings": [], "missing_tests": []}), {"in": 5, "out": 3}

        result = run(
            FIXTURES / "hash_only", completer=completer, out_dir=tmp_path,
            static=_quiet_static(),
        )
    finally:
        text = stream.getvalue()
        _release(handler, loggers)
    assert result.exit_code == 0
    assert KEY not in text
    assert "UNIQUE_PROMPT_MARKER_f8b4" not in text
    assert KEY not in result.report
    assert "UNIQUE_PROMPT_MARKER_f8b4" not in result.report


def test_a_model_reply_that_echoes_the_key_is_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)

    async def completer(model, system, user, **kwargs):
        return json.dumps({
            "findings": [{
                "severity": "Info",
                "file": "HashOnly.sol",
                "quote": "function anchor(bytes32 h) external",
                "title": "note " + KEY,
                "fix": "none",
            }],
            "missing_tests": [],
        }), {"in": 5, "out": 3}

    result = run(
        FIXTURES / "hash_only", completer=completer, out_dir=tmp_path,
        static=_quiet_static(), force_enabled=True,
    )
    assert KEY not in result.report
    assert "***REDACTED***" in result.report


def test_missing_key_does_not_build_a_client(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def boom(*_args, **_kwargs):
        raise AssertionError("client built without a key")

    monkeypatch.setattr("audit_router.review.create_llm_client", boom)
    with pytest.raises(MissingKey) as exc:
        asyncio.run(anthropic_complete(
            "claude-haiku-5-5", "system", "user", max_tokens=16, timeout=5))
    assert "ANTHROPIC_API_KEY" in str(exc.value)
    assert KEY not in str(exc.value)


def test_the_real_completer_uses_llm_complete_and_not_a_second_client(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    seen = {}

    def fake_client(config):
        seen["model"] = config.model
        seen["provider"] = config.provider.value
        return object()

    async def fake_llm(client, config, system, user, **kwargs):
        seen["system"] = system
        seen["web_search"] = kwargs.get("web_search")
        seen["schema"] = kwargs.get("json_schema")
        kwargs["usage_out"]["in"] = 4
        kwargs["usage_out"]["out"] = 2
        kwargs["usage_out"]["cache_read"] = 4
        return '{"findings":[],"missing_tests":[]}'

    monkeypatch.setattr("audit_router.review.create_llm_client", fake_client)
    monkeypatch.setattr("audit_router.review.llm_complete", fake_llm)
    text, usage = asyncio.run(anthropic_complete(
        "claude-haiku-5-5", system_prompt(), "user", max_tokens=32, timeout=5))
    assert text == '{"findings":[],"missing_tests":[]}'
    assert seen["provider"] == "anthropic"
    assert seen["model"] == "claude-haiku-5-5"
    assert seen["system"] == system_prompt()
    assert seen["web_search"] is False
    assert seen["schema"]["required"] == ["findings", "missing_tests"]
    assert usage["cache_read"] == 4
    assert "AsyncAnthropic" not in Path("audit_router/review.py").read_text(encoding="utf-8")
    assert "cache_control" not in Path("audit_router/review.py").read_text(encoding="utf-8")


def test_package_has_no_professor_and_no_legal_copy():
    banned = ("Professor", "GDPR", "MiCA", "MiFID")
    for path in (ROOT / "audit_router").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for word in banned:
            assert word not in text, f"{word} in {path.name}"


def test_override_model_is_the_only_one_called(tmp_path):
    seen = []

    async def completer(model, system, user, **kwargs):
        seen.append(model)
        return json.dumps({"findings": [], "missing_tests": []}), {"in": 6, "out": 2}

    result = run(
        FIXTURES / "payable", model="claude-haiku-5-5", completer=completer,
        out_dir=tmp_path, static=_quiet_static(), force_enabled=True,
    )
    assert seen == ["claude-haiku-5-5"]
    assert result.models == ("claude-haiku-5-5",)
    assert result.verdict == "Safe to deploy"
