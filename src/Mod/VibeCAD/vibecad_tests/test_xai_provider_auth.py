# SPDX-License-Identifier: LGPL-2.1-or-later

"""xAI provider credential resolution does not reuse the OpenAI keyring slot."""

from pathlib import Path

from VibeCADAuth import (
    PROVIDERS,
    read_dotenv_key,
    resolve_auth_credential,
)


def test_xai_is_a_first_class_provider() -> None:
    assert "xai" in PROVIDERS
    spec = PROVIDERS["xai"]
    assert spec.env_var == "XAI_API_KEY"
    assert spec.keyring_username == "xai-api-key"
    assert spec.default_base_url == "https://api.x.ai/v1"


def test_openai_provider_still_ignores_xai_env_name() -> None:
    assert PROVIDERS["openai"].env_var == "OPENAI_API_KEY"
    assert "XAI_API_KEY" not in PROVIDERS["openai"].credential_env_vars()


def test_dotenv_reads_xai_api_key(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("XAI_API_KEY=xai-test-secret\n", encoding="utf-8")
    assert read_dotenv_key(dotenv, provider="xai") == "xai-test-secret"
    assert read_dotenv_key(dotenv, provider="openai") is None


def test_resolve_xai_prefers_dotenv_over_openai_env(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("XAI_API_KEY=xai-from-file\n", encoding="utf-8")
    cred = resolve_auth_credential(
        env={"OPENAI_API_KEY": "sk-openai-only"},
        dotenv_path=dotenv,
        provider="xai",
    )
    assert cred is not None
    assert cred.value == "xai-from-file"
    assert str(dotenv) in cred.source


def test_openai_resolve_does_not_take_xai_dotenv(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("XAI_API_KEY=xai-from-file\n", encoding="utf-8")
    cred = resolve_auth_credential(
        env={},
        dotenv_path=dotenv,
        provider="openai",
    )
    assert cred is None or cred.value != "xai-from-file"
