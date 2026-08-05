"""
Tests for ProviderChain's opt-in payload-size provider eligibility check
(estimated_prompt_tokens/provider_token_limits on generate_json/_run_cascade)
-- added so SE Documentation's targeted rewrite never sends a request already
known to exceed Groq's org tokens-per-minute limit (observed live: HTTP 413,
"Requested 29987" against a 12,000 TPM limit). Both parameters default to
None, so every existing caller that doesn't pass them is unaffected -- see
ProviderChainFallbackOrderingTests in test_llm_provider_json_reliability.py
for that untouched default behavior.
"""

from __future__ import annotations

import unittest

from app.services.llm_provider import BaseProvider, LLMResult, ProviderChain, groq_request_token_limit


class _FakeProvider(BaseProvider):
    def __init__(self, name: str, result: LLMResult | None = None):
        self.name = name
        self._result = result
        self.called = False

    def generate_json(self, prompt, *, use_search=False, max_tokens=None, reporter=None, schema_description=None, **kwargs):
        self.called = True
        return self._result


def _ok(provider: str) -> LLMResult:
    return LLMResult(ok=True, provider=provider, model="test-model", text="", data={"a": 1})


class ProviderEligibilityTests(unittest.TestCase):
    # 12: Groq is skipped when the estimated payload exceeds its configured limit.
    def test_groq_skipped_when_estimated_tokens_exceed_configured_limit(self):
        deepinfra = _FakeProvider("deepinfra", _ok("deepinfra"))
        # If Groq were called it would "succeed" -- proving the skip is what
        # keeps it out of the result, not a scripted failure.
        groq = _FakeProvider("groq", _ok("groq"))

        # DeepInfra fails so the chain reaches Groq's position; Groq must be
        # skipped for size rather than tried and reached.
        deepinfra._result = LLMResult(ok=False, provider="deepinfra", model=None, text="", data=None, error="down")

        chain = ProviderChain(providers=[deepinfra, groq])
        result = chain.generate_json(
            "prompt",
            estimated_prompt_tokens=20000,
            provider_token_limits={"groq": 12000},
        )

        self.assertFalse(groq.called)
        self.assertFalse(result.ok)
        self.assertIn("provider_ineligible_for_payload_size", result.error)

    # 13: Groq remains eligible when a targeted payload fits.
    def test_groq_remains_eligible_when_estimate_fits_configured_limit(self):
        deepinfra = _FakeProvider(
            "deepinfra",
            LLMResult(ok=False, provider="deepinfra", model=None, text="", data=None, error="down"),
        )
        groq = _FakeProvider("groq", _ok("groq"))

        chain = ProviderChain(providers=[deepinfra, groq])
        result = chain.generate_json(
            "prompt",
            estimated_prompt_tokens=5000,
            provider_token_limits={"groq": 12000},
        )

        self.assertTrue(groq.called)
        self.assertTrue(result.ok)
        self.assertEqual(result.provider, "groq")

    # 14: the skip is recorded as payload ineligibility, not a provider failure.
    def test_skip_reason_is_payload_ineligibility_not_generic_failure(self):
        deepinfra = _FakeProvider(
            "deepinfra",
            LLMResult(ok=False, provider="deepinfra", model=None, text="", data=None, error="down"),
        )
        groq = _FakeProvider("groq", _ok("groq"))
        ollama = _FakeProvider(
            "ollama",
            LLMResult(ok=False, provider="ollama", model=None, text="", data=None, error="down"),
        )

        chain = ProviderChain(providers=[deepinfra, groq, ollama])
        result = chain.generate_json(
            "prompt",
            estimated_prompt_tokens=20000,
            provider_token_limits={"groq": 12000},
        )

        self.assertFalse(groq.called)
        self.assertIn("provider_ineligible_for_payload_size", result.error)
        self.assertNotIn("groq -> [", result.error)  # not formatted as a normal provider error

    # A provider not named in provider_token_limits (DeepInfra, Ollama) is
    # never skipped for size, regardless of the estimate.
    def test_providers_without_a_configured_limit_are_never_skipped_for_size(self):
        deepinfra = _FakeProvider("deepinfra", _ok("deepinfra"))
        groq = _FakeProvider("groq", _ok("groq"))

        chain = ProviderChain(providers=[deepinfra, groq])
        result = chain.generate_json(
            "prompt",
            estimated_prompt_tokens=999999,
            provider_token_limits={"groq": 12000},
        )

        self.assertTrue(deepinfra.called)
        self.assertTrue(result.ok)
        self.assertEqual(result.provider, "deepinfra")

    # Omitting both opt-in params (every existing caller) is completely
    # unaffected -- Groq is tried even for a huge estimate-equivalent prompt.
    def test_omitting_eligibility_params_preserves_existing_behavior(self):
        deepinfra = _FakeProvider(
            "deepinfra",
            LLMResult(ok=False, provider="deepinfra", model=None, text="", data=None, error="down"),
        )
        groq = _FakeProvider("groq", _ok("groq"))

        chain = ProviderChain(providers=[deepinfra, groq])
        result = chain.generate_json("prompt")

        self.assertTrue(groq.called)
        self.assertTrue(result.ok)

    def test_groq_request_token_limit_has_a_safe_configurable_default(self):
        self.assertEqual(groq_request_token_limit(), 12000)


if __name__ == "__main__":
    unittest.main()
