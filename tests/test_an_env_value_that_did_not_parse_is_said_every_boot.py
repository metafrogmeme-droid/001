"""A numeric env value that will not parse is its default, and the boot says so.

`config._env_float` (and `_env_float_bounded` through it) answered a value that
would not parse with the default and nothing else -- `MAX_POSITION_PCT=13%`,
a typo in a risk limit, ran 13.0 with nothing said anywhere, on 236 knob reads.
The non-finite branch already warned. Both record the key, the reason and the
default in `config.ENV_UNREAD` now, and `boot_health.env_preflight` -- the
report `main.py` prints on every Telegram boot -- names each one, because a
warning at import is a line in a container log nobody reads.
"""
from __future__ import annotations

import inspect
import logging

import pytest

from bot import config
from bot.core import boot_health
from tests.source_scan import code_only


@pytest.fixture
def clean_record():
    before = list(config.ENV_UNREAD)
    config.ENV_UNREAD.clear()
    try:
        yield config.ENV_UNREAD
    finally:
        config.ENV_UNREAD[:] = before


class TestTheReader:

    def test_a_typo_is_the_default_said_and_recorded(self, monkeypatch, caplog, clean_record):
        monkeypatch.setenv("MAX_POSITION_PCT", "13%")
        with caplog.at_level(logging.WARNING, logger="bot.config"):
            assert config._env_float("MAX_POSITION_PCT", 13.0) == 13.0
        assert clean_record == [("MAX_POSITION_PCT", "not a number", 13.0)]
        (rec,) = [r for r in caplog.records if "MAX_POSITION_PCT" in r.getMessage()]
        assert "not a number" in rec.getMessage() and "13.0" in rec.getMessage()

    def test_the_bounded_reader_goes_through_it(self, monkeypatch, clean_record):
        monkeypatch.setenv("MAX_LEVERAGE", "10x")
        assert config._env_float_bounded("MAX_LEVERAGE", 10, 1, 125) == 10
        assert clean_record == [("MAX_LEVERAGE", "not a number", 10)]

    def test_a_non_finite_value_is_recorded_too(self, monkeypatch, clean_record):
        monkeypatch.setenv("MAX_DAILY_LOSS_PCT", "nan")
        assert config._env_float("MAX_DAILY_LOSS_PCT", 5.0) == 5.0
        assert clean_record == [("MAX_DAILY_LOSS_PCT", "not finite", 5.0)]

    def test_a_value_that_parses_records_nothing(self, monkeypatch, clean_record):
        monkeypatch.setenv("MAX_POSITION_PCT", "12.5")
        assert config._env_float("MAX_POSITION_PCT", 13.0) == 12.5
        monkeypatch.delenv("MAX_POSITION_PCT")
        assert config._env_float("MAX_POSITION_PCT", 13.0) == 13.0
        assert clean_record == []

    def test_the_warning_names_the_key_and_never_the_text(self, monkeypatch, caplog, clean_record):
        monkeypatch.setenv("SOME_KNOB", "SECRETVALUE%")
        with caplog.at_level(logging.WARNING, logger="bot.config"):
            config._env_float("SOME_KNOB", 1.0)
        assert "SECRETVALUE" not in caplog.text and "SOME_KNOB" in caplog.text


class TestTheBootSaysSo:

    def test_the_report_carries_each_unread_value_with_its_default(self):
        report = boot_health.env_preflight(
            {"TELEGRAM_BOT_TOKEN": "t", "BOT_SYNC_SECRET": "s", "WEB_GATEWAY_SECRET": "w",
             "DASHBOARD_TOKEN": "d"},
            unread=[("MAX_POSITION_PCT", "not a number", 13.0)])
        assert report["unread"] == ["MAX_POSITION_PCT is not a number; the default 13.0 is in force"]
        line = boot_health.format_preflight(report)
        assert "values that could not be read: MAX_POSITION_PCT is not a number" in line
        assert "the default 13.0 is in force" in line
        assert "all critical and important secrets present" not in line

    def test_nothing_unread_is_the_sentence_it_always_was(self):
        report = boot_health.env_preflight({}, unread=())
        assert report["unread"] == []
        clean = {"critical": [], "important": [], "unread": []}
        assert boot_health.format_preflight(clean) == \
            "env preflight: all critical and important secrets present."

    def test_an_older_report_without_the_key_still_formats(self):
        assert "all critical" in boot_health.format_preflight({"critical": [], "important": []})

    def test_main_hands_the_record_in_and_says_it(self):
        """A scan, stated as one: the boot block sits inside `main()` behind
        argument parsing and a `sys.exit`, and the claim is that it hands
        `ENV_UNREAD` to the preflight and prints and audits the row."""
        from bot import main as boot
        src = code_only(inspect.getsource(boot.main))
        assert "env_preflight(os.environ, unread=ENV_UNREAD)" in src
        assert 'if _pf["unread"]:' in src
        block = src[src.index('if _pf["unread"]:'):]
        assert 'result="ENV_UNREAD"' in block and "print(" in block.split("run_telegram()")[0]


class TestAnOptionalKnobHasNoDefaultToName:
    """A junk optional dollar cap is treated as unset: the shared budget
    bounds the tier. It was recorded with a 0.0 default, so the boot said
    "the default 0.0 is in force" over a value in force that was None, and
    a cap of zero would turn the tier off."""

    @pytest.mark.parametrize("raw, reason", [
        ("abc", "not a number"), ("-1", "not a finite number at least zero"),
        ("nan", "not a finite number at least zero"),
    ])
    def test_a_junk_cap_is_unset_and_said_as_unset(self, monkeypatch, caplog, clean_record,
                                                    raw, reason):
        monkeypatch.setenv("LLM_DAILY_BUDGET_CHAT_USD", raw)
        with caplog.at_level(logging.WARNING, logger="bot.config"):
            assert config._env_budget_opt("LLM_DAILY_BUDGET_CHAT_USD") is None
        assert clean_record == [("LLM_DAILY_BUDGET_CHAT_USD", reason, None)]
        (rec,) = [r for r in caplog.records if "LLM_DAILY_BUDGET_CHAT_USD" in r.getMessage()]
        assert "treated as unset" in rec.getMessage()
        assert "default" not in rec.getMessage()

    def test_the_boot_says_unset_and_names_no_default(self):
        report = boot_health.env_preflight(
            {}, unread=[("LLM_DAILY_BUDGET_CHAT_USD", "not a number", None),
                        ("MAX_POSITION_PCT", "not a number", 13.0)])
        assert report["unread"] == [
            "LLM_DAILY_BUDGET_CHAT_USD is not a number; it is treated as unset",
            "MAX_POSITION_PCT is not a number; the default 13.0 is in force",
        ]

    def test_a_valid_cap_records_nothing(self, monkeypatch, clean_record):
        monkeypatch.setenv("LLM_DAILY_BUDGET_CHAT_USD", "0.5")
        assert config._env_budget_opt("LLM_DAILY_BUDGET_CHAT_USD") == 0.5
        assert clean_record == []
