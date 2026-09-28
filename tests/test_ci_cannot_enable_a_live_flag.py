"""CI cannot enable a live flag.

CI is paper by DEFAULT: `LIVE_TRADING_ENABLED` defaults False, `SIMULATION_MODE`
defaults True, and no job sets either. Nothing pinned that. A workflow edit that
put `LIVE_TRADING_ENABLED: "true"` in a job's `env:` -- or `export
SIMULATION_MODE=false` in a `run:` step -- would run the red team, the custody
red team and the whole suite against a bot configured to place real orders, on
a runner holding whatever secrets that job was given. The default is the only
thing standing between CI and a live account, and a default is not a guard.

The FAMILY is derived from `bot/config.py`'s own `_env_bool` declarations --
every boolean flag whose name carries LIVE, plus `SIMULATION_MODE` -- rather
than listed, so a live flag added tomorrow is covered the day it is declared,
and the four that make a bot live are pinned so a rename fails here instead of
quietly dropping out of the family. The truth vocabulary is `_env_bool`'s own
(`""`, `"false"`, `"0"`, `"no"` read False; an EMPTY value on a default-True
switch reads True, the C2-07 safety rule), pinned against the reader's source
so this copy cannot drift from the one the bot uses.

Two readings, because a flag reaches a job two ways: an `env:` mapping at
workflow, job or step level, and an ASSIGNMENT inside a `run:` body
(`export X=1`, `X=true python ...`, `echo X=true >> .env`). What is not read
is stated: a `with:` block handing environment to a composite or docker action
is not walked, and a secret's VALUE cannot be read here at all -- so the rule
is over the workflow text, and `${{ secrets.X }}` as a live flag's value is
refused as a live value, because a secret's whole point is that this file
cannot know what it holds.

The real workflows are clean, so every branch of the rule is driven on a
PLANTED workflow: a rule no input can reach is a claim that there is a check.
"""
from __future__ import annotations

import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / "bot" / "config.py"
BOT = ROOT / "bot"
WORKFLOWS = ROOT / ".github" / "workflows"

#: `_env_bool`'s own falsy vocabulary, pinned against its source below.
FALSY = ("", "false", "0", "no")
#: The switch whose LIVE direction is False. Every other door is live when True.
DEFAULT_TRUE = ("SIMULATION_MODE",)

#: A NAME cannot say whether a flag is a door or a protection:
#: `LIVE_TRADING_ENABLED` opens the live path and `LIVE_BOOK_RISK_GATES_ENABLED`
#: guards it, and refusing CI from turning a protection ON would be a wrong
#: claim. So the family is DERIVED (a new LIVE flag is seen the day it is read)
#: and every member must be CLASSIFIED here -- a door the guard refuses in its
#: live direction, or a protection excluded WITH its reason. An unclassified
#: flag fails `test_every_live_flag_is_classified`; a row naming a flag nothing
#: reads any more fails it too, the `known_failures.txt` rule.
DOORS = (
    "LIVE_TRADING_ENABLED",          # the operator's live switch
    "SIMULATION_MODE",               # OFF is live
    "PER_USER_LIVE_ENABLED",         # builds per-user LIVE executors
    "WEB_LIVE_TRADING_ENABLED",      # the website's live-order door
    "AUTO_CONFIRM_LIVE_ENABLED",     # auto-execution on the live path
    "LIVE_OPEN_TO_KEY_HOLDERS",      # widens who may go live
    "LIVE_TRADER_TELEGRAM_IDS",      # the allowlist of who may go live
    "WEB3_LIVE_EXEC_ENABLED",        # on-chain execution
    "WEB3_LIVE_EXEC_SIGN_ENABLED",   # on-chain signing
    "WEB3_LIVE_EXEC_ALLOW_MAINNET",  # on-chain, on a real chain
)
PROTECTIONS = {
    "LIVE_BOOK_RISK_GATES_ENABLED": "the live-book risk gates; ON tightens",
    "LIVE_PERFORMANCE_GOVERNOR_ENABLED": "the realized-performance governor; ON tightens",
    "LIVE_RISK_HARDENING_ENABLED": "the live risk hardening bundle; ON tightens",
    "PERSIST_LIVE_DRAWDOWN_PEAK": "persists the drawdown high-water mark; ON tightens",
    "TIME_STOP_LIVE_AUTO_CLOSE": "the live time-stop auto-close; ON tightens",
}
#: The four that make a bot live; the derived family must hold each.
MUST_HOLD = ("LIVE_TRADING_ENABLED", "SIMULATION_MODE",
             "PER_USER_LIVE_ENABLED", "WEB_LIVE_TRADING_ENABLED")

#: The three spellings a flag is read with. `_env_bool("X")` in config.py is
#: one; `e.get("X", "")` / `os.environ["X"]` / `os.getenv("X")` are the others,
#: and `WEB_LIVE_TRADING_ENABLED` is read ONLY the second way
#: (`bot/web/web_live_gate.py`), so a family derived from `_env_bool` alone
#: had a hole exactly where the guard would matter.
_READ = re.compile(r'(?:_env_bool|_env|\.get|environ\[|getenv)\(\s*"([A-Z][A-Z0-9_]*)"')


def live_family(bot_dir: pathlib.Path) -> tuple[str, ...]:
    """Every env flag `bot/` reads whose name carries LIVE, plus SIMULATION_MODE."""
    names: set[str] = set()
    for py in bot_dir.rglob("*.py"):
        names.update(_READ.findall(py.read_text(encoding="utf-8", errors="replace")))
    return tuple(sorted(n for n in names if "LIVE" in n or n == "SIMULATION_MODE"))


def reads_live(flag: str, raw) -> bool:
    """Would `_env_bool(flag)` read this value in the LIVE direction?

    A secret reference is refused as live: this file cannot know its value.
    """
    s = str(raw).strip()
    if "${{" in s:
        return True
    s = s.strip("'\"").strip().lower()
    if flag in DEFAULT_TRUE:
        # Live means the switch is OFF. An empty value reads True (C2-07).
        return s in FALSY and s != ""
    return s not in FALSY


def _env_blocks(node, path=""):
    """Every `env:` mapping in the workflow, with where it sits."""
    if isinstance(node, dict):
        env = node.get("env")
        if isinstance(env, dict):
            yield path or "workflow", env
        for k, v in node.items():
            if k == "env":
                continue
            yield from _env_blocks(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _env_blocks(v, f"{path}[{i}]")


def _run_bodies(node, path=""):
    if isinstance(node, dict):
        run = node.get("run")
        if isinstance(run, str):
            yield path or "workflow", run
        for k, v in node.items():
            yield from _run_bodies(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _run_bodies(v, f"{path}[{i}]")


def live_flags_in(workflow_text: str, family: tuple[str, ...]) -> list[str]:
    """Every place this workflow turns a family flag to its live direction."""
    doc = yaml.safe_load(workflow_text) or {}
    found: list[str] = []
    for where, env in _env_blocks(doc):
        for k, v in env.items():
            if k in family and reads_live(k, v):
                found.append(f"env {where}: {k}={v!r}")
    assign = re.compile(r"\b(" + "|".join(map(re.escape, family)) + r")\s*=\s*(\S+)")
    for where, body in _run_bodies(doc):
        for m in assign.finditer(body):
            if reads_live(m.group(1), m.group(2)):
                found.append(f"run {where}: {m.group(0)}")
    return found


# --- the real tree -----------------------------------------------------------

def test_no_workflow_enables_a_live_flag():
    family = tuple(n for n in live_family(BOT) if n in DOORS)
    offenders = {}
    for wf in sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml")):
        hits = live_flags_in(wf.read_text(encoding="utf-8"), family)
        if hits:
            offenders[wf.name] = hits
    assert not offenders, (
        "a GitHub Actions workflow turns a live flag on; CI is paper-only and "
        f"the suite would run against a bot configured to place orders: {offenders}")


def test_every_live_flag_is_classified():
    family = set(live_family(BOT))
    unclassified = sorted(family - set(DOORS) - set(PROTECTIONS))
    assert not unclassified, (
        f"{unclassified} is read under bot/ and carries LIVE in its name, and "
        "this guard does not know whether it is a door or a protection; "
        "classify it in DOORS or PROTECTIONS (with its reason)")
    stale = sorted((set(DOORS) | set(PROTECTIONS)) - family)
    assert not stale, f"{stale} is classified here but nothing under bot/ reads it"
    assert not set(DOORS) & set(PROTECTIONS)
    assert all(PROTECTIONS.values()), "a protection row must carry its reason"


def test_the_family_is_derived_and_holds_the_four_that_make_a_bot_live():
    family = live_family(BOT)
    missing = [n for n in MUST_HOLD if n not in family]
    assert not missing, (f"{missing} is no longer read anywhere under bot/; "
                         "a renamed live flag has dropped out of this guard")


def test_the_truth_vocabulary_is_the_readers_own():
    src = CONFIG.read_text(encoding="utf-8")
    assert 'raw in ("", "false", "0", "no")' in src, (
        "_env_bool's falsy vocabulary moved; FALSY above is a copy of it and "
        "must move with it")
    assert set(FALSY) == {"", "false", "0", "no"}


# --- planted workflows: the rule is measured where it is the only thing in play

FAMILY = ("LIVE_TRADING_ENABLED", "SIMULATION_MODE", "PER_USER_LIVE_ENABLED")


def _wf(env_yaml: str = "", run: str = "echo ok", job_env: str = "") -> str:
    return f"""
name: planted
on: push
jobs:
  t:
    runs-on: ubuntu-latest
{job_env}
    steps:
      - name: step
{env_yaml}
        run: |
          {run}
"""


def test_a_step_env_that_turns_live_on_is_named():
    wf = _wf(env_yaml='        env:\n          LIVE_TRADING_ENABLED: "true"')
    hits = live_flags_in(wf, FAMILY)
    assert len(hits) == 1 and "LIVE_TRADING_ENABLED='true'" in hits[0], hits


def test_a_job_env_that_switches_simulation_off_is_named():
    wf = _wf(job_env='    env:\n      SIMULATION_MODE: "false"')
    hits = live_flags_in(wf, FAMILY)
    assert len(hits) == 1 and "SIMULATION_MODE='false'" in hits[0], hits


def test_an_unquoted_yaml_boolean_is_read():
    wf = _wf(env_yaml="        env:\n          PER_USER_LIVE_ENABLED: true")
    hits = live_flags_in(wf, FAMILY)
    assert len(hits) == 1 and "PER_USER_LIVE_ENABLED=True" in hits[0], hits


def test_an_export_in_a_run_step_is_named():
    hits = live_flags_in(_wf(run="export LIVE_TRADING_ENABLED=1 && python3 -m bot.main"), FAMILY)
    assert hits == ["run jobs.t.steps[0]: LIVE_TRADING_ENABLED=1"], hits


def test_a_dotenv_written_by_a_step_is_named():
    hits = live_flags_in(_wf(run='echo "SIMULATION_MODE=false" >> .env'), FAMILY)
    assert len(hits) == 1 and "SIMULATION_MODE=false" in hits[0], hits


def test_a_secret_as_a_live_flags_value_is_refused():
    wf = _wf(env_yaml="        env:\n          LIVE_TRADING_ENABLED: ${{ secrets.LIVE }}")
    hits = live_flags_in(wf, FAMILY)
    assert len(hits) == 1, hits


def test_the_paper_direction_is_not_named():
    wf = _wf(env_yaml='        env:\n          LIVE_TRADING_ENABLED: "false"\n'
                      '          SIMULATION_MODE: "true"\n'
                      '          PER_USER_LIVE_ENABLED: "0"',
             run="export LIVE_TRADING_ENABLED=no")
    assert live_flags_in(wf, FAMILY) == []


def test_an_empty_simulation_mode_reads_as_paper():
    """C2-07: an empty value on a default-True safety switch reads True."""
    wf = _wf(env_yaml='        env:\n          SIMULATION_MODE: ""')
    assert live_flags_in(wf, FAMILY) == []
    assert reads_live("SIMULATION_MODE", "") is False
    assert reads_live("LIVE_TRADING_ENABLED", "") is False


def test_a_flag_outside_the_family_is_not_read():
    wf = _wf(env_yaml='        env:\n          LIVE_TRADING_ENABLED_NOTE: "true"',
             run="export SOMETHING_LIVE_TRADING_ENABLED=true")
    assert live_flags_in(wf, FAMILY) == []
