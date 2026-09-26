from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare_web_env.py"
SPEC = importlib.util.spec_from_file_location("prepare_web_env", SCRIPT)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def _bot_env(**overrides: str) -> dict[str, str]:
    values = {
        "PUBLIC_GATEWAY_URL": "https://gateway.example.test/",
        "BOT_GATEWAY_URL": "http://127.0.0.1:8080",
        "BOT_SYNC_SECRET": "s" * 32,
        "WEB_GATEWAY_SECRET": "g" * 32,
        "WEB_CREDS_KEY": "0123456789abcdef" * 4,
        "JWT_SECRET": "j" * 32,
        "APP_BASE_URL": "https://app.example.test",
    }
    values.update(overrides)
    return values


def test_both_web_links_are_derived_from_the_public_tunnel():
    out = mod.build_web_env(_bot_env(), {})
    assert out["BOT_GATEWAY_URL"] == "https://gateway.example.test"
    assert out["BOT_API_URL"] == "https://gateway.example.test"
    assert "127.0.0.1" not in out["BOT_GATEWAY_URL"]


def test_existing_web_only_settings_survive_regeneration():
    out = mod.build_web_env(_bot_env(), {
        "DATABASE_URL": "mysql://managed-by-the-page",
        "SOCIAL_X_URL": "https://social.example.test",
        "BOT_API_URL": "http://localhost:8000",
    })
    assert out["DATABASE_URL"] == "mysql://managed-by-the-page"
    assert out["SOCIAL_X_URL"] == "https://social.example.test"
    assert out["BOT_API_URL"] == "https://gateway.example.test"


@pytest.mark.parametrize("url", [
    "http://gateway.example.test",
    "https://127.0.0.1:8080",
    "https://localhost:8080",
    "https://10.0.0.2",
    "not-a-url",
])
def test_non_public_gateway_is_refused(url):
    with pytest.raises(ValueError):
        mod.build_web_env(_bot_env(PUBLIC_GATEWAY_URL=url), {})


def test_write_is_private_and_atomic(tmp_path):
    destination = tmp_path / "web.env"
    mod.write_atomic(destination, {"B": "2", "A": "1"})
    assert destination.read_text(encoding="utf-8") == "A=1\nB=2\n"
    assert os.stat(destination).st_mode & 0o777 == 0o600


def test_every_bot_deploy_regenerates_the_persistent_web_environment():
    deploy = (SCRIPT.parents[1] / "deploy.sh").read_text(encoding="utf-8")
    assert "scripts/prepare_web_env.py" in deploy
    assert '"$PERSIST_DIR/web.env"' in deploy
