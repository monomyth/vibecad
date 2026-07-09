# SPDX-License-Identifier: LGPL-2.1-or-later

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from urllib import error

from VibeCADAuth import (
    AuthStatus,
    KEYRING_SERVICE,
    KEYRING_USERNAME,
    PROVIDERS,
    delete_keyring_key,
    list_provider_models,
    provider_spec,
    read_dotenv_key,
    read_keyring_key,
    redact_secret,
    resolve_auth_state,
    store_keyring_key,
    validate_api_key,
    validate_configured_auth,
)

from vibecad_tests.support import (
    FakeHTTPResponse,
    FakeJSONResponse,
    FakeKeyringModule,
)

class TestVibeCADAuth(unittest.TestCase):
    def test_redacts_api_key(self):
        self.assertEqual(redact_secret("sk-test123456"), "sk-...3456")

    def test_resolves_environment_key_without_exposing_secret(self):
        state = resolve_auth_state(env={"OPENAI_API_KEY": "sk-test123456"})
        self.assertEqual(state.status, AuthStatus.CONFIGURED_UNVERIFIED)
        self.assertEqual(state.source, "environment")
        self.assertNotIn("test123456", state.redacted_key)

    def test_reads_dotenv_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("OPENAI_API_KEY='sk-test123456'\n", encoding="utf-8")
            self.assertEqual(read_dotenv_key(path), "sk-test123456")

    def test_keyring_storage_is_used_without_exposing_secret(self):
        original = sys.modules.get("keyring")
        fake = FakeKeyringModule()
        sys.modules["keyring"] = fake
        try:
            stored = store_keyring_key("sk-test123456")
            self.assertTrue(stored["stored"])
            self.assertEqual(stored["redacted_key"], "sk-...3456")
            self.assertEqual(
                fake.values[(KEYRING_SERVICE, KEYRING_USERNAME)],
                "sk-test123456",
            )
            self.assertEqual(read_keyring_key(), "sk-test123456")
            state = resolve_auth_state(env={})
            self.assertEqual(state.status, AuthStatus.CONFIGURED_UNVERIFIED)
            self.assertEqual(state.source, "OS keyring")
            self.assertNotIn("test123456", state.redacted_key)
            deleted = delete_keyring_key()
            self.assertTrue(deleted["deleted"])
            self.assertIsNone(read_keyring_key())
        finally:
            if original is None:
                sys.modules.pop("keyring", None)
            else:
                sys.modules["keyring"] = original

    def test_keyring_absence_fails_without_plaintext_fallback(self):
        with mock.patch("VibeCADAuth._keyring_module", return_value=None):
            stored = store_keyring_key("sk-test123456")
            self.assertFalse(stored["stored"])
            self.assertIsNone(stored["redacted_key"])

    def test_validate_api_key_openai_reports_verified_without_exposing_secret(self):
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return FakeHTTPResponse(200)

        state = validate_api_key(
            "sk-test123456",
            provider="openai",
            source="unit-test",
            timeout_seconds=0.5,
            opener=opener,
        )
        self.assertEqual(state.status, AuthStatus.VERIFIED)
        self.assertEqual(state.source, "unit-test")
        self.assertEqual(state.redacted_key, "sk-...3456")
        self.assertNotIn("test123456", state.message)
        self.assertEqual(requests[0][0].full_url, "https://api.openai.com/v1/models")
        self.assertEqual(requests[0][0].headers["Authorization"], "Bearer sk-test123456")

    def test_validate_api_key_openai_reports_invalid_and_offline(self):
        def invalid_opener(_request, timeout=None):
            raise error.HTTPError(
                "https://api.openai.com/v1/models",
                401,
                "Unauthorized",
                {},
                None,
            )

        invalid = validate_api_key(
            "sk-test123456",
            provider="openai",
            timeout_seconds=0.5,
            opener=invalid_opener,
        )
        self.assertEqual(invalid.status, AuthStatus.INVALID)
        self.assertNotIn("test123456", str(invalid))

        def offline_opener(_request, timeout=None):
            raise OSError("network unavailable")

        offline = validate_api_key(
            "sk-test123456",
            provider="openai",
            timeout_seconds=0.5,
            opener=offline_opener,
        )
        self.assertEqual(offline.status, AuthStatus.OFFLINE)
        self.assertNotIn("test123456", str(offline))

    def test_validate_configured_auth_uses_resolved_credential(self):
        state = validate_configured_auth(
            provider="openai",
            env={"OPENAI_API_KEY": "sk-test123456"},
            timeout_seconds=0.5,
            opener=lambda _request, timeout=None: FakeHTTPResponse(200),
        )
        self.assertEqual(state.status, AuthStatus.VERIFIED)
        self.assertEqual(state.source, "environment")

    def test_validate_configured_auth_supports_anthropic_provider(self):
        state = validate_configured_auth(
            provider="anthropic",
            env={"ANTHROPIC_API_KEY": "sk-ant-test123456"},
            timeout_seconds=0.5,
            opener=lambda _request, timeout=None: FakeHTTPResponse(200),
        )
        self.assertEqual(state.status, AuthStatus.VERIFIED)
        self.assertEqual(state.source, "environment")
        self.assertIn("Anthropic", state.message)

    def test_keyring_storage_is_provider_scoped(self):
        original = sys.modules.get("keyring")
        fake = FakeKeyringModule()
        sys.modules["keyring"] = fake
        try:
            stored = store_keyring_key("sk-ant-key1234", provider="anthropic")
            self.assertTrue(stored["stored"])
            self.assertEqual(
                fake.values[(KEYRING_SERVICE, "anthropic-api-key")],
                "sk-ant-key1234",
            )
            self.assertIsNone(read_keyring_key())
            self.assertEqual(
                read_keyring_key(provider="anthropic"), "sk-ant-key1234"
            )
            deleted = delete_keyring_key(provider="anthropic")
            self.assertTrue(deleted["deleted"])
            self.assertIsNone(read_keyring_key(provider="anthropic"))
        finally:
            if original is None:
                sys.modules.pop("keyring", None)
            else:
                sys.modules["keyring"] = original

    def test_models_url_for_derives_provider_specific_endpoints(self):
        openai = provider_spec("openai")
        anthropic = provider_spec("anthropic")

        # Blank/None overrides fall back to the official endpoints.
        self.assertEqual(openai.models_url_for(None), "https://api.openai.com/v1/models")
        self.assertEqual(openai.models_url_for(""), "https://api.openai.com/v1/models")
        self.assertEqual(
            openai.models_url_for("   "), "https://api.openai.com/v1/models"
        )
        self.assertEqual(
            anthropic.models_url_for(None), "https://api.anthropic.com/v1/models"
        )

        # OpenAI convention: base URL includes the /v1 segment.
        self.assertEqual(
            openai.models_url_for("http://localhost:8000/v1"),
            "http://localhost:8000/v1/models",
        )
        self.assertEqual(
            openai.models_url_for("http://localhost:8000/v1/"),
            "http://localhost:8000/v1/models",
        )

        # Anthropic convention: base URL excludes the /v1 segment.
        self.assertEqual(
            anthropic.models_url_for("http://localhost:9000"),
            "http://localhost:9000/v1/models",
        )
        self.assertEqual(
            anthropic.models_url_for("http://localhost:9000/"),
            "http://localhost:9000/v1/models",
        )

    def test_validate_api_key_requests_overridden_base_url(self):
        seen = []

        def opener(http_request, timeout=None):
            seen.append(http_request)
            return FakeHTTPResponse(200)

        state = validate_api_key(
            "sk-test123456",
            provider="openai",
            timeout_seconds=0.5,
            opener=opener,
            base_url="http://localhost:8000/v1",
        )
        self.assertEqual(state.status, AuthStatus.VERIFIED)
        self.assertEqual(seen[0].full_url, "http://localhost:8000/v1/models")
        self.assertEqual(seen[0].headers["Authorization"], "Bearer sk-test123456")

        state = validate_api_key(
            "sk-ant-test123456",
            provider="anthropic",
            timeout_seconds=0.5,
            opener=opener,
            base_url="http://localhost:9000/",
        )
        self.assertEqual(state.status, AuthStatus.VERIFIED)
        self.assertEqual(seen[1].full_url, "http://localhost:9000/v1/models")
        self.assertEqual(seen[1].headers["X-api-key"], "sk-ant-test123456")

    def test_validate_configured_auth_forwards_base_url(self):
        seen = []

        def opener(http_request, timeout=None):
            seen.append(http_request)
            return FakeHTTPResponse(200)

        state = validate_configured_auth(
            provider="openai",
            env={"OPENAI_API_KEY": "sk-test123456"},
            timeout_seconds=0.5,
            opener=opener,
            base_url="http://localhost:8000/v1",
        )
        self.assertEqual(state.status, AuthStatus.VERIFIED)
        self.assertEqual(seen[0].full_url, "http://localhost:8000/v1/models")

    def test_list_provider_models_requests_overridden_base_url(self):
        openai_urls = []

        def openai_opener(http_request, timeout=None):
            openai_urls.append(http_request.full_url)
            return FakeJSONResponse({"data": [{"id": "local-model"}]})

        payload = list_provider_models(
            "sk-test123456",
            provider="openai",
            opener=openai_opener,
            base_url="http://localhost:8000/v1",
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["models"], ["local-model"])
        self.assertEqual(openai_urls, ["http://localhost:8000/v1/models"])

        pages = [
            {"data": [{"id": "claude-local"}], "has_more": True, "last_id": "claude-local"},
            {"data": [{"id": "claude-local-2"}], "has_more": False},
        ]
        anthropic_urls = []

        def anthropic_opener(http_request, timeout=None):
            anthropic_urls.append(http_request.full_url)
            return FakeJSONResponse(pages[len(anthropic_urls) - 1])

        payload = list_provider_models(
            "sk-ant-test123456",
            provider="anthropic",
            opener=anthropic_opener,
            base_url="http://localhost:9000",
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["models"], ["claude-local", "claude-local-2"])
        self.assertEqual(len(anthropic_urls), 2)
        for url in anthropic_urls:
            self.assertTrue(url.startswith("http://localhost:9000/v1/models?"))
        self.assertIn("after_id=claude-local", anthropic_urls[1])

    def test_provider_registry_covers_openai_and_anthropic(self):
        self.assertEqual(set(PROVIDERS), {"openai", "anthropic"})
        spec = provider_spec("anthropic")
        self.assertEqual(spec.env_var, "ANTHROPIC_API_KEY")
        self.assertEqual(spec.models_url, "https://api.anthropic.com/v1/models")
        headers = spec.auth_headers("sk-ant-test123456")
        self.assertEqual(headers["x-api-key"], "sk-ant-test123456")
        self.assertIn("anthropic-version", headers)
        self.assertNotIn("Authorization", headers)
        openai_headers = provider_spec("openai").auth_headers("sk-test123456")
        self.assertEqual(openai_headers["Authorization"], "Bearer sk-test123456")
        with self.assertRaises(ValueError):
            provider_spec("not-a-provider")

    def test_resolves_anthropic_environment_and_dotenv_keys(self):
        state = resolve_auth_state(
            env={"ANTHROPIC_API_KEY": "sk-ant-test123456"},
            provider="anthropic",
        )
        self.assertEqual(state.status, AuthStatus.CONFIGURED_UNVERIFIED)
        self.assertEqual(state.source, "environment")
        self.assertNotIn("test123456", state.redacted_key)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "OPENAI_API_KEY='sk-openai-key99'\n"
                "ANTHROPIC_API_KEY='sk-ant-key1234'\n",
                encoding="utf-8",
            )
            self.assertEqual(
                read_dotenv_key(path, provider="anthropic"), "sk-ant-key1234"
            )
            self.assertEqual(read_dotenv_key(path), "sk-openai-key99")

    def test_validate_anthropic_api_key_uses_native_headers(self):
        requests_seen = []

        def opener(http_request, timeout=None):
            requests_seen.append(http_request)
            return FakeHTTPResponse(200)

        state = validate_api_key(
            "sk-ant-test123456",
            provider="anthropic",
            source="unit-test",
            timeout_seconds=0.5,
            opener=opener,
        )
        self.assertEqual(state.status, AuthStatus.VERIFIED)
        self.assertIn("Anthropic", state.message)
        self.assertNotIn("test123456", state.message)
        sent = requests_seen[0]
        self.assertEqual(sent.full_url, "https://api.anthropic.com/v1/models")
        self.assertEqual(sent.headers["X-api-key"], "sk-ant-test123456")
        self.assertEqual(sent.headers["Anthropic-version"], "2023-06-01")
        self.assertNotIn("Authorization", sent.headers)

    def test_list_provider_models_openai_single_page(self):
        opened = []

        def opener(http_request, timeout=None):
            opened.append(http_request.full_url)
            return FakeJSONResponse({"data": [{"id": "gpt-5.5"}, {"id": "gpt-4o"}]})

        payload = list_provider_models("sk-test123456", provider="openai", opener=opener)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["models"], ["gpt-5.5", "gpt-4o"])
        self.assertIsNone(payload["error"])
        self.assertEqual(opened, ["https://api.openai.com/v1/models"])

    def test_list_provider_models_anthropic_paginates(self):
        pages = [
            {
                "data": [{"id": "claude-sonnet-5"}],
                "has_more": True,
                "last_id": "claude-sonnet-5",
            },
            {
                "data": [{"id": "claude-haiku-4"}],
                "has_more": False,
                "last_id": "claude-haiku-4",
            },
        ]
        urls = []

        def opener(http_request, timeout=None):
            urls.append(http_request.full_url)
            return FakeJSONResponse(pages[len(urls) - 1])

        payload = list_provider_models(
            "sk-ant-test123456", provider="anthropic", opener=opener
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["models"], ["claude-sonnet-5", "claude-haiku-4"])
        self.assertEqual(len(urls), 2)
        self.assertIn("limit=100", urls[0])
        self.assertIn("after_id=claude-sonnet-5", urls[1])

    def test_list_provider_models_reports_errors_without_key_exposure(self):
        def opener(http_request, timeout=None):
            raise error.HTTPError(http_request.full_url, 401, "Unauthorized", {}, None)

        payload = list_provider_models(
            "sk-ant-test123456", provider="anthropic", opener=opener
        )
        self.assertFalse(payload["ok"])
        self.assertIn("401", payload["error"])
        self.assertNotIn("test123456", payload["error"])

        missing = list_provider_models("", provider="anthropic")
        self.assertFalse(missing["ok"])
        self.assertIn("Anthropic", missing["error"])
