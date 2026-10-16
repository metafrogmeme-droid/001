"""The SoloHost package installs the bot it describes, in practice mode only.

`scripts/solohost/` holds the two files a publisher pastes into SoloHost, Pi
Desktop's app framework: `docker-compose.yml` (what to run) and
`config_options.yml` (the install form). SoloHost pulls the image, writes the
form's answers to `.env` and runs `docker compose up -d`. Its contract is
SOLOHOST.md in github.com/pi-node/solohost (v0, read 2026-10-16).

Three kinds of claim live in those files, and each is held here:

  * SoloHost's own rules, which its validator enforces at publish (no `build:`,
    one primary UI service publishing one loopback port, no bind mounts, a
    closed set of form keys, every `${VAR}` declared). A package that breaks
    one is refused at publish, after the operator has already pasted it.
  * The rules SoloHost says it does NOT check and that still decide whether
    the app works: the UI label, and "every option must actually take effect".
    A form field the bot never reads is a dead control; the operator sets it,
    sees nothing change and concludes the bot is broken. So every name the
    form writes must be an environment variable the bot reads.
  * This repository's own: the install is practice only. The switches that
    could arm live trading, auto-confirm a live order or admit strangers are
    fixed values the form never shows, and no exchange credential is asked for.

Each rule is a function returning the problems it finds, driven on planted
packages as well as the real one, because the real one passes and a rule no
input can reach is a claim that there is a check.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "scripts" / "solohost"
COMPOSE = PKG / "docker-compose.yml"
CONFIG = PKG / "config_options.yml"
WORKFLOW = ROOT / ".github" / "workflows" / "image.yml"

#: SOLOHOST.md §1, "Blocked constructs (rejected)", as service keys.
BLOCKED_SERVICE_KEYS = ("privileged", "cap_add", "security_opt", "extends",
                        "userns_mode", "pid", "ipc", "uts", "cgroup", "devices",
                        "build", "network_mode", "container_name")
#: SOLOHOST.md §2: the closed set of top-level keys and field types.
CONFIG_KEYS = {"title", "eyebrow", "description", "footer_hint", "output_file",
               "after_save", "fixed_values", "fields"}
FIELD_TYPES = {"text", "password", "number", "select", "hidden"}
REJECTED_FIELD_KEYS = ("placeholder", "min", "max")

#: The practice-only install: these are written as fixed values, never shown.
FIXED_SAFETY = {
    "SIMULATION_MODE": "true",
    "LIVE_TRADING_ENABLED": "false",
    "AUTO_CONFIRM_LIVE_ENABLED": "false",
    "PAPER_AUTO_ACCEPT": "false",
}
#: Names an install form must never ask for: exchange credentials.
CREDENTIAL = re.compile(r"(BITGET|BINANCE|BYBIT|OKX|EXCHANGE).*|.*(SECRET|PASSPHRASE).*")

VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:[:?-][^}]*)?\}")
LOOPBACK_PORT = re.compile(r"^127\.0\.0\.1:(\d+):(\d+)$")
RELEASE_TAG = re.compile(r"^ghcr\.io/metafrogmeme-droid/runeclaw:v\d+\.\d+\.\d+$")


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _ui_services(compose: dict[str, Any]) -> list[str]:
    return [name for name, svc in (compose.get("services") or {}).items()
            if str((svc.get("labels") or {}).get("pi.ui.primary", "")).lower() == "true"]


# ── the rules ───────────────────────────────────────────────────────────

def compose_problems(compose: dict[str, Any]) -> list[str]:
    """SoloHost's compose rules, enforced and not."""
    out: list[str] = []
    services = compose.get("services") or {}
    if not services:
        return ["no services"]
    declared_volumes = set((compose.get("volumes") or {}).keys())
    for name, svc in services.items():
        if not svc.get("image"):
            out.append(f"{name}: no image")
        for key in BLOCKED_SERVICE_KEYS:
            if key in svc:
                out.append(f"{name}: blocked key {key}")
        for vol in svc.get("volumes") or []:
            src = str(vol).split(":", 1)[0]
            if src.startswith((".", "/", "~")) or src not in declared_volumes:
                out.append(f"{name}: volume {vol!r} is not a declared named volume")
    ui = _ui_services(compose)
    if len(ui) != 1:
        out.append(f"{len(ui)} services carry pi.ui.primary, need exactly one")
    else:
        ports = services[ui[0]].get("ports") or []
        if len(ports) != 1 or not LOOPBACK_PORT.match(str(ports[0])):
            out.append(f"UI service ports {ports!r}: need one 127.0.0.1:HOST:CONTAINER")
    return out


def config_problems(config: dict[str, Any]) -> list[str]:
    """SoloHost's install-form schema."""
    out: list[str] = []
    for key in set(config) - CONFIG_KEYS:
        out.append(f"unknown top-level key {key}")
    for field in config.get("fields") or []:
        name = field.get("name", "?")
        if field.get("type") not in FIELD_TYPES:
            out.append(f"{name}: type {field.get('type')!r}")
        for key in REJECTED_FIELD_KEYS:
            if key in field:
                out.append(f"{name}: rejected key {key}")
        if "default" in field and not isinstance(field["default"], str):
            out.append(f"{name}: default is not a string")
        if field.get("type") == "password" and field.get("preserve_if_blank") is not True:
            out.append(f"{name}: a re-save would wipe this secret (preserve_if_blank)")
        if field.get("type") == "select":
            opts = field.get("options") or []
            if not opts or any(not {"value", "label"} <= set(o) for o in opts):
                out.append(f"{name}: select options need value and label")
    for fixed in config.get("fixed_values") or []:
        if "value" not in fixed and "detect" not in fixed:
            out.append(f"{fixed.get('name', '?')}: fixed value with neither value nor detect")
    return out


def form_names(config: dict[str, Any]) -> set[str]:
    return ({f["name"] for f in config.get("fields") or []}
            | {f["name"] for f in config.get("fixed_values") or []})


def undeclared_vars(compose_text: str, config: dict[str, Any]) -> list[str]:
    """Every ${VAR} in the compose must be a form field or a fixed value."""
    return sorted(set(VAR.findall(compose_text)) - form_names(config))


def safety_problems(config: dict[str, Any]) -> list[str]:
    """The practice-only install, as fixed values the operator never sees."""
    out: list[str] = []
    fixed = {f["name"]: str(f.get("value")) for f in config.get("fixed_values") or []}
    shown = {f["name"] for f in config.get("fields") or []}
    for name, want in FIXED_SAFETY.items():
        if fixed.get(name) != want:
            out.append(f"{name} is not fixed to {want!r}")
        if name in shown:
            out.append(f"{name} is a field the operator can change")
    for name in form_names(config):
        if CREDENTIAL.fullmatch(name):
            out.append(f"{name} asks for an exchange credential")
    return out


# ── what the bot reads ──────────────────────────────────────────────────

def _bot_env_reads() -> set[str]:
    """Every environment variable name the bot reads by a literal name."""
    pat = re.compile(r"""(?:_env(?:_secret|_bool|_float|_int)?|getenv|environ\.get)\(\s*["']([A-Z0-9_]+)["']""")
    names: set[str] = set()
    for path in (ROOT / "bot").rglob("*.py"):
        names.update(pat.findall(path.read_text(encoding="utf-8", errors="replace")))
    return names


def _dashboard_port() -> int:
    """The port bot/main.py binds the dashboard on, read from the call."""
    src = (ROOT / "bot" / "main.py").read_text(encoding="utf-8")
    m = re.search(r"TCPSite\(dashboard_runner,\s*_dash_host,\s*(\d+)\)", src)
    assert m, "bot/main.py no longer binds the dashboard with TCPSite(...); re-read it"
    return int(m.group(1))


# ── the real package ────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return _load(COMPOSE)


@pytest.fixture(scope="module")
def config() -> dict[str, Any]:
    return _load(CONFIG)


def test_the_compose_meets_solohosts_rules(compose):
    assert compose_problems(compose) == []


def test_the_form_meets_solohosts_schema(config):
    assert config_problems(config) == []


def test_every_compose_variable_is_declared_in_the_form(config):
    assert undeclared_vars(COMPOSE.read_text(encoding="utf-8"), config) == []


def test_the_install_is_practice_only(config):
    assert safety_problems(config) == []


def test_every_form_value_is_something_the_bot_reads(config):
    """SOLOHOST.md: "Every option must actually take effect"."""
    reads = _bot_env_reads()
    dead = sorted(form_names(config) - reads)
    assert not dead, f"the form writes {dead}, which nothing in bot/ reads"


def test_every_form_value_reaches_the_container_under_its_own_name(compose, config):
    """`.env` is not handed to the container by itself: a value reaches the
    bot only through the service's `environment:` block."""
    ui = _ui_services(compose)[0]
    env = compose["services"][ui].get("environment") or {}
    missing = sorted(n for n in form_names(config) if env.get(n) != "${" + n + "}")
    assert not missing, f"form values that never reach the bot: {missing}"


def test_the_ui_port_is_the_one_the_dashboard_binds(compose):
    ui = _ui_services(compose)[0]
    host, container = LOOPBACK_PORT.match(compose["services"][ui]["ports"][0]).groups()
    assert int(container) == _dashboard_port()
    assert int(host) != int(container), "SOLOHOST.md: map a distinctive host port"


def test_the_health_check_asks_the_page_this_package_serves(compose):
    """The image's own HEALTHCHECK asks the API bridge on 8000, which this
    package does not run; an inherited check would mark it unhealthy forever."""
    ui = _ui_services(compose)[0]
    test = " ".join(compose["services"][ui]["healthcheck"]["test"])
    assert f"localhost:{_dashboard_port()}/health" in test
    server = (ROOT / "bot" / "web" / "dashboard_server.py").read_text(encoding="utf-8")
    assert 'add_get("/health"' in server


def test_the_service_runs_the_bot(compose):
    ui = _ui_services(compose)[0]
    assert compose["services"][ui]["command"] == [
        "python3", "-m", "bot.main", "--mode", "telegram"]


def test_the_volumes_hold_what_the_bot_writes(compose):
    ui = _ui_services(compose)[0]
    targets = {str(v).split(":", 1)[1] for v in compose["services"][ui]["volumes"]}
    assert {"/app/data", "/app/logs"} <= targets
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "mkdir -p /app/logs /app/data" in dockerfile


def test_the_image_is_a_release_the_workflow_publishes(compose):
    """SoloHost pulls, never builds, and :latest moves under installs."""
    wf = _load(WORKFLOW)
    image = wf["jobs"]["publish"]["env"]["IMAGE"]
    for name, svc in compose["services"].items():
        assert RELEASE_TAG.match(svc["image"]), f"{name}: {svc['image']}"
        assert svc["image"].rsplit(":", 1)[0] == image


def test_every_ai_provider_offered_is_one_the_bot_knows(config):
    from bot.llm.provider import LLMProvider
    known = {p.value for p in LLMProvider}
    field = next(f for f in config["fields"] if f["name"] == "LLM_PROVIDER")
    offered = {o["value"] for o in field["options"]}
    assert offered <= known, f"offered but unknown to the bot: {sorted(offered - known)}"
    assert field["default"] in offered


def test_the_dashboard_token_is_required(config):
    """Without DASHBOARD_TOKEN the dashboard API refuses every call, so the App
    tab would show a page that can never fill."""
    field = next(f for f in config["fields"] if f["name"] == "DASHBOARD_TOKEN")
    assert field["required"] is True and field["type"] == "password"


# ── driven on planted packages, because the real one passes ─────────────

def _good() -> tuple[dict[str, Any], dict[str, Any]]:
    return copy.deepcopy(_load(COMPOSE)), copy.deepcopy(_load(CONFIG))


def _bot(compose: dict[str, Any]) -> dict[str, Any]:
    return compose["services"][_ui_services(compose)[0]]


@pytest.mark.parametrize("mutate,expect", [
    (lambda c: _bot(c).update(build="."), "blocked key build"),
    (lambda c: _bot(c).update(container_name="rc"), "blocked key container_name"),
    (lambda c: _bot(c).update(privileged=True), "blocked key privileged"),
    (lambda c: _bot(c).pop("image"), "no image"),
    (lambda c: _bot(c)["volumes"].append("./data:/app/data"), "not a declared named volume"),
    (lambda c: _bot(c)["volumes"].append("undeclared:/x"), "not a declared named volume"),
    (lambda c: _bot(c)["labels"].pop("pi.ui.primary"), "need exactly one"),
    (lambda c: _bot(c).update(ports=["18480:8080"]), "need one 127.0.0.1"),
    (lambda c: _bot(c).update(ports=["127.0.0.1:1:8080", "127.0.0.1:2:9090"]), "need one 127.0.0.1"),
])
def test_the_compose_rule_catches(mutate, expect):
    compose, _ = _good()
    mutate(compose)
    assert any(expect in p for p in compose_problems(compose)), compose_problems(compose)


@pytest.mark.parametrize("mutate,expect", [
    (lambda c: c.update(version="1"), "unknown top-level key"),
    (lambda c: c["fields"][0].update(placeholder="x"), "rejected key placeholder"),
    (lambda c: c["fields"][0].update(type="checkbox"), "type"),
    (lambda c: c["fields"][0].pop("preserve_if_blank"), "preserve_if_blank"),
    (lambda c: next(f for f in c["fields"] if f["type"] == "select").update(default=1), "not a string"),
    (lambda c: c["fixed_values"].append({"name": "X"}), "neither value nor detect"),
])
def test_the_form_rule_catches(mutate, expect):
    _, config = _good()
    mutate(config)
    assert any(expect in p for p in config_problems(config)), config_problems(config)


def test_the_undeclared_variable_rule_catches():
    _, config = _good()
    text = COMPOSE.read_text(encoding="utf-8") + "\n# ${NOT_IN_THE_FORM}\n"
    assert undeclared_vars(text, config) == ["NOT_IN_THE_FORM"]


@pytest.mark.parametrize("mutate,expect", [
    (lambda c: next(f for f in c["fixed_values"] if f["name"] == "SIMULATION_MODE").update(value="false"),
     "SIMULATION_MODE is not fixed"),
    (lambda c: c["fixed_values"].remove(next(f for f in c["fixed_values"] if f["name"] == "PAPER_AUTO_ACCEPT")),
     "PAPER_AUTO_ACCEPT is not fixed"),
    (lambda c: c["fields"].append({"name": "LIVE_TRADING_ENABLED", "type": "text"}),
     "LIVE_TRADING_ENABLED is a field"),
    (lambda c: c["fields"].append({"name": "BITGET_API_KEY", "type": "password", "preserve_if_blank": True}),
     "exchange credential"),
])
def test_the_safety_rule_catches(mutate, expect):
    _, config = _good()
    mutate(config)
    assert any(expect in p for p in safety_problems(config)), safety_problems(config)


def test_the_dead_control_rule_catches():
    reads = _bot_env_reads()
    assert "TELEGRAM_BOT_TOKEN" in reads and "PAPER_SIM_OPT_IN_ENABLED" in reads
    assert "SOLOHOST_THEME_COLOUR" not in reads
