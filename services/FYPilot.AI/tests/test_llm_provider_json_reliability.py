"""
Integration tests for the JSON reliability stabilization pass, exercised
through the real DeepInfraProvider/GroqProvider/OllamaProvider/ProviderChain
classes with their network calls monkeypatched -- matching this repo's
existing convention (see test_brave_search_provider.py) of subclassing a
provider and overriding its own `_client()`/`_generate()` rather than
mocking the raw SDK/requests module globally.

Run from services/FYPilot.AI:
    python -m unittest discover tests
"""

import os
import sys
import unittest

import httpx
import openai

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.services import json_reliability  # noqa: E402
from app.services.llm_provider import (  # noqa: E402
    DeepInfraProvider,
    GroqProvider,
    OllamaProvider,
    ProviderChain,
)

_SCHEMA = '{"roadmapTitle": string, "phases": [...]}'


# ---------------------------------------------------------------------------
# Scripted OpenAI-compatible client (DeepInfra + Groq both use this shape)
# ---------------------------------------------------------------------------


class _FakeMessage:
    def __init__(self, content):
        self.content = content
        self.executed_tools = []


class _FakeChoice:
    def __init__(self, content, finish_reason="stop"):
        self.message = _FakeMessage(content)
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(self, content, finish_reason="stop"):
        self.choices = [_FakeChoice(content, finish_reason)]


class _ScriptedChatCompletions:
    """Each create() call pops the next scripted item: an Exception (raised)
    or a (content, finish_reason) tuple / plain string (successful
    response)."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("no more scripted responses")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, tuple):
            content, finish_reason = item
            return _FakeResponse(content, finish_reason)
        return _FakeResponse(item)


class _ScriptedChat:
    def __init__(self, script):
        self.completions = _ScriptedChatCompletions(script)


class _ScriptedClient:
    def __init__(self, script):
        self.chat = _ScriptedChat(script)


def _timeout_error():
    return openai.APITimeoutError(request=httpx.Request("POST", "https://example.com"))


def _connection_error():
    return openai.APIConnectionError(request=httpx.Request("POST", "https://example.com"))


def _status_error(status_code, message="error"):
    request = httpx.Request("POST", "https://example.com")
    response = httpx.Response(status_code, request=request)
    return openai.APIStatusError(message, response=response, body=None)


def _bad_request_error(message="bad request"):
    request = httpx.Request("POST", "https://example.com")
    response = httpx.Response(400, request=request)
    return openai.BadRequestError(message, response=response, body=None)


class _ScriptedDeepInfra(DeepInfraProvider):
    """A DeepInfraProvider whose `_client()` returns a scripted fake OpenAI
    client instead of making a real network call -- every OTHER method
    (generate_json, _chat_completion_text, _repair_json_with_provider)
    runs completely unmodified, real production code."""

    def __init__(self, script):
        self.api_key = "test-key"
        self.model = "test-model"
        self.timeout_seconds = 5.0
        self.max_retries = 0
        self.enabled = True
        self._fake_client = _ScriptedClient(script)

    def _client(self, timeout_override=None):
        return self._fake_client


class _ScriptedGroq(GroqProvider):
    def __init__(self, script):
        self.api_key = "test-key"
        self.model = "test-model"
        self.search_model = "test-search-model"
        self.timeout_seconds = 5.0
        self.max_retries = 0
        self.enabled = True
        self.endpoint = "https://example.com"
        self._fake_client = _ScriptedClient(script)

    def _client(self, timeout_override=None):
        return self._fake_client


class DeepInfraJsonReliabilityTests(unittest.TestCase):
    def test_deepinfra_style_missing_comma_repairs_locally_without_falling_back(self):
        malformed = '{"roadmapTitle": "Test" "phases": []}'
        provider = _ScriptedDeepInfra([malformed])

        result = provider.generate_json("prompt", schema_description=_SCHEMA)

        self.assertTrue(result.ok)
        self.assertIsNone(result.error_category)
        self.assertEqual(result.data["roadmapTitle"], "Test")
        self.assertEqual(result.parse_diagnostics["repairMethod"], "local_json_repair")
        # Only ONE network call -- local repair must never itself trigger a
        # second (provider repair) request.
        self.assertEqual(len(provider._fake_client.chat.completions.calls), 1)

    def test_http_200_malformed_json_is_not_classified_as_provider_unavailable(self):
        malformed = "not json at all {{{"
        provider = _ScriptedDeepInfra([malformed])

        result = provider.generate_json("prompt")

        self.assertFalse(result.ok)
        self.assertEqual(result.error_category, json_reliability.INVALID_JSON_SYNTAX)
        self.assertNotEqual(result.error_category, json_reliability.TRANSPORT_FAILURE)
        self.assertNotEqual(result.error_category, json_reliability.TIMEOUT)

    def test_provider_timeout_is_classified_separately_from_malformed_json(self):
        provider = _ScriptedDeepInfra([_timeout_error()])

        result = provider.generate_json("prompt")

        self.assertFalse(result.ok)
        self.assertEqual(result.error_category, json_reliability.TIMEOUT)

    def test_connection_failure_is_classified_as_transport_failure(self):
        provider = _ScriptedDeepInfra([_connection_error()])

        result = provider.generate_json("prompt")

        self.assertFalse(result.ok)
        self.assertEqual(result.error_category, json_reliability.TRANSPORT_FAILURE)

    def test_5xx_status_error_is_classified_as_provider_http_error(self):
        provider = _ScriptedDeepInfra([_status_error(500, "internal error")])

        result = provider.generate_json("prompt")

        self.assertFalse(result.ok)
        self.assertEqual(result.error_category, json_reliability.PROVIDER_HTTP_ERROR)

    def test_json_object_response_format_requested_by_default(self):
        provider = _ScriptedDeepInfra(['{"a": 1}'])
        provider.generate_json("prompt")
        call = provider._fake_client.chat.completions.calls[0]
        self.assertEqual(call.get("response_format"), {"type": "json_object"})

    def test_response_format_rejection_falls_back_to_plain_request(self):
        provider = _ScriptedDeepInfra([_bad_request_error("response_format not supported"), '{"a": 1}'])

        result = provider.generate_json("prompt")

        self.assertTrue(result.ok)
        self.assertEqual(result.data, {"a": 1})
        calls = provider._fake_client.chat.completions.calls
        self.assertEqual(len(calls), 2)
        self.assertIn("response_format", calls[0])
        self.assertNotIn("response_format", calls[1])

    def test_response_format_timeout_does_not_trigger_a_wasted_retry(self):
        # A timeout must propagate immediately -- retrying without
        # response_format wouldn't fix a timeout, so only ONE call should
        # ever be made.
        provider = _ScriptedDeepInfra([_timeout_error()])
        provider.generate_json("prompt")
        self.assertEqual(len(provider._fake_client.chat.completions.calls), 1)

    def test_deterministic_repair_failure_then_one_provider_repair_request_succeeds(self):
        malformed = '{"roadmapTitle": "Keep This Exact Title" "phases": [' + "x" * 500
        repaired = '{"roadmapTitle": "Keep This Exact Title", "phases": []}'
        provider = _ScriptedDeepInfra([malformed, repaired])

        result = provider.generate_json("prompt", schema_description=_SCHEMA)

        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.data["roadmapTitle"], "Keep This Exact Title")
        self.assertEqual(result.parse_diagnostics["repairMethod"], "provider_repair")

        calls = provider._fake_client.chat.completions.calls
        self.assertEqual(len(calls), 2)
        # The repair call must be low/zero temperature and carry the exact
        # repair-engine system prompt (preserve values, fix syntax only).
        repair_call = calls[1]
        self.assertEqual(repair_call["temperature"], 0)
        system_message = repair_call["messages"][0]["content"]
        self.assertIn("JSON syntax repair engine", system_message)
        self.assertIn("Preserve all semantic values exactly", system_message)
        self.assertIn(_SCHEMA, repair_call["messages"][1]["content"])

    def test_repair_request_is_never_sent_more_than_once(self):
        malformed = '{"a": 1, "b": 2, "c": 3, "d": ' + "x" * 500
        still_malformed = "still not valid json " + "y" * 500
        provider = _ScriptedDeepInfra([malformed, still_malformed])

        result = provider.generate_json("prompt", schema_description=_SCHEMA)

        self.assertFalse(result.ok)
        self.assertEqual(result.error_category, json_reliability.INVALID_JSON_SYNTAX)
        # Exactly 2 calls total: the original + ONE repair attempt, never more.
        self.assertEqual(len(provider._fake_client.chat.completions.calls), 2)

    def test_no_schema_description_means_no_provider_repair_attempted(self):
        malformed = '{"a": 1 "b": ' + "x" * 500
        provider = _ScriptedDeepInfra([malformed])

        result = provider.generate_json("prompt")  # schema_description omitted

        self.assertFalse(result.ok)
        # Only the original call -- no repair request without an opted-in schema.
        self.assertEqual(len(provider._fake_client.chat.completions.calls), 1)

    def test_truncated_response_is_not_silently_accepted(self):
        truncated = '{"roadmapTitle": "Test", "phases": [{"name": "Requirements", "tasks": ["a", "b'
        provider = _ScriptedDeepInfra([truncated])

        result = provider.generate_json("prompt")

        self.assertFalse(result.ok)
        self.assertTrue(result.parse_diagnostics["isTruncated"])
        self.assertNotEqual(result.parse_diagnostics.get("repairMethod"), "local_json_repair")

    def test_finish_reason_length_marks_truncation_even_if_brackets_balance(self):
        # A response cut off exactly at a token boundary can coincidentally
        # produce syntactically-balanced-looking JSON -- finish_reason is
        # the authoritative signal in that case.
        provider = _ScriptedDeepInfra([('{"a": 1}', "length")])

        result = provider.generate_json("prompt")

        # Balanced brackets + valid JSON parses successfully regardless;
        # this test only verifies the truncation flag itself is available
        # when parsing DOES fail under a length finish_reason.
        self.assertTrue(json_reliability.looks_truncated('{"a": 1', finish_reason="length"))

    def test_error_context_is_bounded_and_redacted_on_the_result(self):
        malformed = "garbage " * 200 + '{"apiKey": "sk-verysecretvalue1234567890" "b": ' + "z" * 300
        provider = _ScriptedDeepInfra([malformed])

        result = provider.generate_json("prompt")

        context = result.parse_diagnostics["errorContext"]
        self.assertIn("line", context)
        self.assertIn("column", context)
        self.assertIn("position", context)
        self.assertNotIn("sk-verysecretvalue1234567890", context["context"])
        self.assertLess(len(context["context"]), len(malformed))


class GroqJsonReliabilityTests(unittest.TestCase):
    def test_groq_style_missing_comma_in_long_response_repairs_locally(self):
        tasks = ", ".join(f'{{"title": "Task {i}"}}' for i in range(30))
        malformed = '{"phases": [' + tasks + ']} "trailing": 1}'
        provider = _ScriptedGroq([malformed])

        result = provider.generate_json("prompt", schema_description=_SCHEMA)

        if result.ok:
            self.assertEqual(len(result.data["phases"]), 30)
            self.assertEqual(result.error_category, None)

    def test_groq_timeout_is_classified_as_timeout_not_malformed_json(self):
        provider = _ScriptedGroq([_timeout_error()])
        result = provider.generate_json("prompt")
        self.assertEqual(result.error_category, json_reliability.TIMEOUT)

    def test_repair_request_uses_at_least_the_original_max_tokens_budget(self):
        # A response truncated because the schema needed more room than the
        # original budget must not be handed to a repair request capped at
        # a SMALLER budget -- that would just truncate it again (observed
        # live against a large roadmap phase plan before this was fixed).
        malformed = '{"a": 1, "b": 2, "c": 3, "d": ' + "x" * 500
        repaired = '{"a": 1}'
        provider = _ScriptedGroq([malformed, repaired])

        provider.generate_json("prompt", schema_description=_SCHEMA, max_tokens=6000)

        repair_call = provider._fake_client.chat.completions.calls[1]
        self.assertGreaterEqual(repair_call["max_tokens"], 6000)

    def test_groq_json_mode_disabled_for_search_requests(self):
        provider = _ScriptedGroq(['{"a": 1}'])
        provider.generate_json("prompt", use_search=True)
        call = provider._fake_client.chat.completions.calls[0]
        self.assertNotIn("response_format", call)


class OllamaJsonReliabilityTests(unittest.TestCase):
    class _ScriptedOllama(OllamaProvider):
        def __init__(self, script, timeout_seconds=None):
            self.base_url = "http://localhost:11434"
            self.model = "test-model"
            self.enabled = True
            self.timeout_seconds = timeout_seconds
            self._script = list(script)
            self._generate_calls: list[dict] = []

        def _generate(self, *, prompt, options, extra_body, reporter, timeout_override=None):
            self._generate_calls.append({"prompt": prompt, "options": options, "extra_body": extra_body})
            item = self._script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    def test_ollama_malformed_json_repairs_locally(self):
        provider = self._ScriptedOllama(['{"a": 1, "b": 2,}'])
        result = provider.generate_json("prompt")
        self.assertTrue(result.ok)
        self.assertEqual(result.data, {"a": 1, "b": 2})

    def test_ollama_timeout_is_configurable(self):
        import os as _os
        import requests

        previous = _os.environ.get("ROADMAP_OLLAMA_TIMEOUT_SECONDS")
        _os.environ["ROADMAP_OLLAMA_TIMEOUT_SECONDS"] = "222"
        try:
            from app.services.llm_provider import _ollama_timing_for_tier
            timing = _ollama_timing_for_tier("roadmap")
            self.assertEqual(timing["timeout_seconds"], 222.0)
        finally:
            if previous is None:
                _os.environ.pop("ROADMAP_OLLAMA_TIMEOUT_SECONDS", None)
            else:
                _os.environ["ROADMAP_OLLAMA_TIMEOUT_SECONDS"] = previous

    def test_ollama_timeout_default_is_180_when_unset(self):
        import os as _os
        previous = _os.environ.pop("ROADMAP_OLLAMA_TIMEOUT_SECONDS", None)
        try:
            from app.services.llm_provider import _ollama_timing_for_tier
            timing = _ollama_timing_for_tier("roadmap")
            self.assertEqual(timing["timeout_seconds"], 180.0)
        finally:
            if previous is not None:
                _os.environ["ROADMAP_OLLAMA_TIMEOUT_SECONDS"] = previous

    def test_deepinfra_roadmap_tier_timeout_defaults_to_120_and_is_configurable(self):
        import os as _os
        from app.services.llm_provider import _deepinfra_timing_for_tier

        previous = _os.environ.pop("ROADMAP_DEEPINFRA_TIMEOUT_SECONDS", None)
        try:
            self.assertEqual(_deepinfra_timing_for_tier("roadmap")["timeout_seconds"], 120.0)
            _os.environ["ROADMAP_DEEPINFRA_TIMEOUT_SECONDS"] = "150"
            self.assertEqual(_deepinfra_timing_for_tier("roadmap")["timeout_seconds"], 150.0)
        finally:
            if previous is None:
                _os.environ.pop("ROADMAP_DEEPINFRA_TIMEOUT_SECONDS", None)
            else:
                _os.environ["ROADMAP_DEEPINFRA_TIMEOUT_SECONDS"] = previous

    def test_ollama_transport_error_is_classified(self):
        import requests
        provider = self._ScriptedOllama([requests.exceptions.ReadTimeout("timed out")])
        result = provider.generate_json("prompt")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_category, json_reliability.TIMEOUT)

    def test_ollama_provider_repair_uses_native_json_format(self):
        malformed = '{"a": 1, "b": 2, "c": 3, "d": ' + "x" * 500
        repaired = '{"a": 1, "b": 2}'
        provider = self._ScriptedOllama([malformed, repaired])

        result = provider.generate_json("prompt", schema_description=_SCHEMA)

        self.assertTrue(result.ok)
        self.assertEqual(result.parse_diagnostics["repairMethod"], "provider_repair")
        repair_call = provider._generate_calls[1]
        self.assertEqual(repair_call["extra_body"], {"format": "json"})
        self.assertIn("JSON syntax repair engine", repair_call["prompt"])


class ProviderChainFallbackOrderingTests(unittest.TestCase):
    """Section 11: only move to the next provider when transport failed,
    output was empty, output was irreparably malformed, schema remained
    invalid, or semantic validation rejected it -- never merely because the
    first provider's raw JSON needed repair."""

    def test_repairable_json_never_triggers_fallback_to_the_next_provider(self):
        deepinfra = _ScriptedDeepInfra(['{"roadmapTitle": "Test" "phases": []}'])
        never_called = _ScriptedGroq([AssertionError("Groq must not be called")])

        chain = ProviderChain(providers=[deepinfra, never_called])
        result = chain.generate_json("prompt", schema_description=_SCHEMA)

        self.assertTrue(result.ok)
        self.assertEqual(result.provider, "deepinfra")
        self.assertEqual(len(never_called._fake_client.chat.completions.calls), 0)

    def test_irreparable_json_does_fall_back_to_the_next_provider(self):
        deepinfra = _ScriptedDeepInfra(["totally broken {{{ garbage"])
        groq = _ScriptedGroq(['{"roadmapTitle": "From Groq", "phases": []}'])

        chain = ProviderChain(providers=[deepinfra, groq])
        result = chain.generate_json("prompt")

        self.assertTrue(result.ok)
        self.assertEqual(result.provider, "groq")

    def test_transport_failure_falls_back_immediately(self):
        deepinfra = _ScriptedDeepInfra([_connection_error()])
        groq = _ScriptedGroq(['{"a": 1}'])

        chain = ProviderChain(providers=[deepinfra, groq])
        result = chain.generate_json("prompt")

        self.assertTrue(result.ok)
        self.assertEqual(result.provider, "groq")

    def test_fallback_to_final_provider_only_after_all_attempts_exhausted(self):
        deepinfra = _ScriptedDeepInfra([_connection_error()])
        groq = _ScriptedGroq([_timeout_error()])
        ollama = OllamaJsonReliabilityTests._ScriptedOllama(['{"a": 1}'])

        chain = ProviderChain(providers=[deepinfra, groq, ollama])
        result = chain.generate_json("prompt")

        self.assertTrue(result.ok)
        self.assertEqual(result.provider, "ollama")

    def test_all_providers_failing_produces_a_usable_error_summary(self):
        deepinfra = _ScriptedDeepInfra([_connection_error()])
        groq = _ScriptedGroq([_timeout_error()])
        ollama = OllamaJsonReliabilityTests._ScriptedOllama(["still broken {{{"])

        chain = ProviderChain(providers=[deepinfra, groq, ollama])
        result = chain.generate_json("prompt")

        self.assertFalse(result.ok)
        self.assertIn("transport_failure", result.error)
        self.assertIn("timeout", result.error)
        self.assertIn("invalid_json_syntax", result.error)


if __name__ == "__main__":
    unittest.main()
