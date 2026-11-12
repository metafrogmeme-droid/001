"""scripts/bot_status.py: a read-only report on the bot's processes.

It runs only read-only commands, it prints no secret, it names a second
``bot.main``, and a check it could not run is never shown as a pass. Every
command goes through an injected runner here; nothing is started, signalled
or reached over a network."""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "bot_status.py"


def _load():
    spec = importlib.util.spec_from_file_location("bot_status", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bot_status"] = mod
    spec.loader.exec_module(mod)
    return mod


bs = _load()
OWN = 4242
READ_ONLY_PROGRAMS = {"bash", "pgrep", "ps", "journalctl"}


class Box:
    """A machine as the runner sees it, recording every command asked."""

    def __init__(self, status=(0, "OK  runeclaw-bot  restarts: 0\n"), pids=None, journal=None,
                 missing=(), args=None):
        self.args = args or {}
        self.status = status
        self.pids = {"bot\\.main": ["101"], "api_bridge": ["202"]} if pids is None else pids
        self.journal = journal or {}
        self.missing = set(missing)
        self.calls: list[list[str]] = []

    def __call__(self, cmd):
        cmd = list(cmd)
        self.calls.append(cmd)
        prog = cmd[0]
        if prog in self.missing:
            return 127, f"{prog}: not available"
        if prog == "bash":
            return self.status
        if prog == "pgrep":
            got = self.pids.get(cmd[-1], [])
            return (0, "\n".join(got) + "\n") if got else (1, "")
        if prog == "ps":
            if cmd[1:3] == ["-o", "args="]:
                return 0, self.args.get(cmd[-1], "python3 -m bot.main --mode telegram") + "\n"
            return 0, "93784\n"
        if prog == "journalctl":
            unit = cmd[cmd.index("-u") + 1]
            text = self.journal.get(unit)
            return (0, text) if text else (0, "-- No entries --\n")
        raise AssertionError(f"unexpected command {cmd}")


def answering(url):
    return 403 if "8080" in url else 200


def report(tmp_path, box=None, prober=answering, logs=True, **kw):
    tmp_path.mkdir(parents=True, exist_ok=True)
    if logs:
        (tmp_path / "bot.log").write_text("started\nall good\n")
    (tmp_path / "scripts/systemd").mkdir(parents=True, exist_ok=True)
    (tmp_path / "scripts/systemd/runeclaw-status.sh").write_text("#!/bin/bash\n")
    box = box or Box()
    rep = bs.build(tmp_path, runner=box, prober=prober, own_pid=OWN, **kw)
    return rep, "\n".join(rep.lines), box


# ── read only, by construction ─────────────────────────────────────────────

def test_every_command_it_runs_is_a_read_only_one(tmp_path):
    _, _, box = report(tmp_path, box=Box(journal={"runeclaw-bot": "line\n"}))
    assert {c[0] for c in box.calls} <= READ_ONLY_PROGRAMS
    bash = [c for c in box.calls if c[0] == "bash"]
    assert bash and all(c[1].endswith("scripts/systemd/runeclaw-status.sh") for c in bash)
    for c in box.calls:
        if c[0] == "journalctl":
            assert "-n" in c and "--no-pager" in c and not {"--rotate", "--vacuum-size", "--flush"} & set(c)


def test_the_source_starts_signals_and_writes_nothing():
    tree = ast.parse(SCRIPT.read_text())
    forbidden_calls = {"kill", "killpg", "send_signal", "terminate", "unlink", "remove", "rmtree",
                       "write_text", "write_bytes", "rename", "replace", "chmod", "touch", "mkdir",
                       "Popen", "system", "execv", "execvp", "spawnv"}
    lists = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            assert name not in forbidden_calls, f"bot_status.py calls {name}"
            if name == "open":
                modes = [a.value for a in node.args if isinstance(a, ast.Constant)]
                assert modes and all(m in ("r", "rb") for m in modes), "a file opened for anything but reading"
        if isinstance(node, ast.List) and node.elts and isinstance(node.elts[0], ast.Constant) \
                and isinstance(node.elts[0].value, str):
            lists.append(node.elts[0].value)
    commands = [s for s in lists if s in READ_ONLY_PROGRAMS or s in {"systemctl", "nohup", "pkill", "kill"}]
    assert commands and set(commands) <= READ_ONLY_PROGRAMS, commands
    assert not any(".env" in f for f in bs.LOG_FILES), "no env file is read by default"


# ── outcomes ───────────────────────────────────────────────────────────────

def test_a_healthy_box_is_exit_0_and_a_403_from_the_gateway_is_an_answer(tmp_path):
    rep, text, _ = report(tmp_path)
    assert rep.worst == bs.OK, text
    assert "answering      bot gateway: HTTP 403" in text
    assert "running  bot (bot.main): pid 101 up 1d02h03m" in text
    assert "VERDICT: up, serving" in text


def test_two_bot_main_processes_are_named_and_nothing_is_chosen_to_kill(tmp_path):
    box = Box(pids={"bot\\.main": ["101", "303"], "api_bridge": ["202"]})
    rep, text, box = report(tmp_path, box=box)
    assert rep.worst == bs.WRONG and "TWO+     bot (bot.main): 2 running" in text
    assert all(c[0] in READ_ONLY_PROGRAMS for c in box.calls)


def test_a_missing_always_on_process_is_down_and_an_optional_one_is_not(tmp_path):
    rep, text, _ = report(tmp_path, box=Box(pids={"api_bridge": ["202"]}))
    assert rep.worst == bs.WRONG and "DOWN     bot (bot.main): not running" in text
    assert "absent   Daily Duel bot (bot.duel_bot): not running (optional" in text
    rep2, _, _ = report(tmp_path / "b")
    assert rep2.worst == bs.OK, "optional bots absent is the expected state"


def test_its_own_process_is_not_counted(tmp_path):
    box = Box(pids={"bot\\.main": [str(OWN), "101"], "api_bridge": ["202"]})
    rep, text, _ = report(tmp_path, box=box)
    assert rep.worst == bs.OK and f"pid {OWN}" not in text


def test_a_process_running_this_script_is_not_counted_as_the_bot(tmp_path):
    box = Box(pids={"bot\\.main": ["101", "555"], "api_bridge": ["202"]},
              args={"555": "python3 scripts/bot_status.py --log /tmp/bot.main.log"})
    rep, text, _ = report(tmp_path, box=box)
    assert rep.worst == bs.OK and "pid 555" not in text and "TWO+" not in text


def test_a_port_that_does_not_answer_is_wrong_only_when_it_must_answer(tmp_path):
    rep, text, _ = report(tmp_path, prober=lambda u: None if "8080" in u else 200)
    assert rep.worst == bs.WRONG and "NOT ANSWERING  bot gateway" in text
    rep2, text2, _ = report(tmp_path / "b", prober=lambda u: None if "808" in u and "8080" not in u else 200)
    assert rep2.worst == bs.OK and "not answering  Daily Duel bot (optional)" in text2


def test_a_check_that_could_not_run_is_unknown_never_a_pass(tmp_path):
    rep, text, _ = report(tmp_path, box=Box(missing={"pgrep"}))
    assert rep.worst == bs.UNKNOWN and "pgrep is not available" in text
    assert "VERDICT: some checks could not run" in text


def test_could_not_tell_never_softens_a_failure(tmp_path):
    box = Box(status=(1, "DOWN  runeclaw-bot\n"), missing={"pgrep"})
    rep, _, _ = report(tmp_path, box=box)
    assert rep.worst == bs.WRONG


def test_the_supervisor_s_verdict_is_carried(tmp_path):
    rep, text, _ = report(tmp_path, box=Box(status=(1, "CRASHLOOP?  runeclaw-bot  restarts: 212\n")))
    assert rep.worst == bs.WRONG and "restarts: 212" in text and "exit 1: something is wrong" in text
    rep3, _, _ = report(tmp_path / "c", box=Box(status=(3, "UNKNOWN: systemctl is not available\n")))
    assert rep3.worst == bs.UNKNOWN


def test_no_log_anywhere_is_unknown(tmp_path):
    rep, text, _ = report(tmp_path, logs=False)
    assert rep.worst == bs.UNKNOWN and "nothing was read" in text


# ── no secret is printed ───────────────────────────────────────────────────

def test_every_secret_shape_in_a_log_is_scrubbed_before_it_is_printed(tmp_path):
    from bot.utils.secret_shapes import drop_url_queries, scrub_secrets, table_rows
    examples = [r["example"] for r in table_rows()]
    (tmp_path / "bot.log").write_text("\n".join(examples) + "\n")
    _, text, _ = report(tmp_path, logs=False, lines=200)
    for ex in examples:
        clean = drop_url_queries(scrub_secrets(ex))
        assert clean != ex, f"the table's own example is not changed by its scrub: {ex[:40]}"
        assert ex not in text, f"printed unscrubbed: {ex[:40]}"
        assert clean[:400] in text


def test_journal_lines_are_scrubbed_too(tmp_path):
    token = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw0"
    box = Box(journal={"runeclaw-bot": f"telegram error https://api.telegram.org/bot{token}/getUpdates\n"})
    _, text, _ = report(tmp_path, box=box)
    assert token not in text and "journal runeclaw-bot" in text


def test_an_env_file_passed_as_a_log_is_refused_unread(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=do-not-print-me\n")
    _, text, _ = report(tmp_path, extra=[env])
    assert "refused" in text and "do-not-print-me" not in text


# ── tails ──────────────────────────────────────────────────────────────────

def test_the_tail_is_the_last_lines_and_the_count_is_capped(tmp_path):
    p = tmp_path / "big.log"
    p.write_text("".join(f"line {i}\n" for i in range(50_000)))
    assert bs.tail_file(p, 3) == ["line 49997", "line 49998", "line 49999"]
    _, text, _ = report(tmp_path, extra=[p], lines=10_000)
    assert "line 49799" not in text and "line 49800" in text, "at most 200 lines a source"


def test_the_last_error_looking_line_is_pulled_out(tmp_path):
    (tmp_path / "bot.log").write_text("ok\nTraceback (most recent call last):\nValueError: boom\nok\n")
    _, text, _ = report(tmp_path, logs=False)
    assert "(1 error-looking line(s)" in text and ">> Traceback" in text


@pytest.mark.parametrize("doc", [".claude/skills/bot-status/SKILL.md", "docs/BOT_STATUS_RUNBOOK.md"])
def test_the_skill_and_runbook_name_the_script_and_forbid_acting(doc):
    text = (ROOT / doc).read_text()
    assert "python3 scripts/bot_status.py" in text
    assert "restart" in text.lower() and ("never" in text.lower() or "do not" in text.lower())
