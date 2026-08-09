"""
Coverage for AnthropicProvider's JSON-syntax repair path -- the layer that
truncated live on 2026-08-06 (a scoped SE Documentation rewrite/structural-
repair candidate hit its max_tokens ceiling, and the repair attempt made to
fix it ALSO truncated, silently, because the repair budget reused the same
ceiling and the failure telemetry couldn't distinguish "repair also
truncated" from "repair produced different malformed JSON").

Exercised through the real AnthropicProvider/ProviderChain classes with the
Anthropic SDK client monkeypatched -- matching this repo's existing
convention (see test_llm_provider_json_reliability.py) of subclassing a
provider and overriding its own `_client()` rather than mocking the SDK
module globally. No live Anthropic calls are made anywhere in this file.
"""

import os
import sys
import unittest

_SERVICE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from app.services import json_reliability  # noqa: E402
from app.services.llm_provider import AnthropicProvider, _repair_max_tokens  # noqa: E402

_SCHEMA = '{"database": {"databaseEntities": [...]}}'


def _substantial_truncated_candidate(entity_count=8):
    # Padded well past json_reliability.is_substantial's 400-char/6-quote
    # threshold -- a short truncated fragment is deliberately never handed
    # to the provider-repair path at all (see is_substantial's docstring),
    # so a test candidate must look like a genuinely large cut-off document
    # to exercise the repair path this file is testing.
    entities = ", ".join(
        f'{{"entityId": "ENT-{i:02d}", "name": "Entity{i}", "purpose": "Sample purpose text"}}'
        for i in range(entity_count)
    )
    return '{"database": {"databaseEntities": [' + entities + ', {"entityId": "ENT-CUT'


# ---------------------------------------------------------------------------
# Scripted Anthropic Messages API client
# ---------------------------------------------------------------------------


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeUsage:
    def __init__(self, input_tokens, output_tokens, thinking_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.output_tokens_details = _FakeUsageDetails(thinking_tokens)


class _FakeUsageDetails:
    def __init__(self, thinking_tokens):
        self.thinking_tokens = thinking_tokens


class _FakeAnthropicResponse:
    def __init__(self, text, *, stop_reason="end_turn", input_tokens=100, output_tokens=50):
        self.content = [_FakeTextBlock(text)]
        self.stop_reason = stop_reason
        self.usage = _FakeUsage(input_tokens, output_tokens)


class _ScriptedMessages:
    """Each create() call pops the next scripted item: an Exception (raised)
    or a _FakeAnthropicResponse."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("no more scripted Anthropic responses")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _ScriptedAnthropicClient:
    def __init__(self, script):
        self.messages = _ScriptedMessages(script)


class _ScriptedAnthropicProvider(AnthropicProvider):
    """Real AnthropicProvider with only the SDK client swapped out --
    generate_json/_repair_json_with_provider run unmodified."""

    def __init__(self, script):
        super().__init__(model="claude-sonnet-5")
        self.enabled = True
        self._script = list(script)
        self._scripted_client = _ScriptedAnthropicClient(self._script)

    def _client(self, timeout_override=None):
        return self._scripted_client

    @property
    def calls(self):
        return self._scripted_client.messages.calls


class RepairMaxTokensTests(unittest.TestCase):
    def test_gives_fifty_percent_headroom_over_the_original_budget(self):
        # A repair call must reproduce the ENTIRE malformed candidate plus
        # finish/close whatever was cut off -- reusing the SAME budget the
        # original call already exhausted guarantees the repair truncates
        # too (see this module's docstring). 1.5x is the fix.
        self.assertEqual(_repair_max_tokens(14000), 21000)
        self.assertEqual(_repair_max_tokens(8000), 12000)

    def test_never_goes_below_the_2200_floor(self):
        self.assertEqual(_repair_max_tokens(100), 2200)
        self.assertEqual(_repair_max_tokens(0), 2200)


class AnthropicRepairTelemetryTests(unittest.TestCase):
    def test_original_truncated_repair_succeeds(self):
        truncated = _FakeAnthropicResponse(
            _substantial_truncated_candidate(),
            stop_reason="max_tokens", output_tokens=14000,
        )
        repaired = _FakeAnthropicResponse(
            '{"database": {"databaseEntities": [{"entityId": "ENT-01", "name": "User"}]}}',
            stop_reason="end_turn", output_tokens=1200,
        )
        provider = _ScriptedAnthropicProvider([truncated, repaired])

        result = provider.generate_json("prompt", schema_description=_SCHEMA)

        self.assertTrue(result.ok)
        self.assertEqual(result.data["database"]["databaseEntities"][0]["name"], "User")
        self.assertEqual(len(provider.calls), 2)
        # The repair call must ask for MORE than the original's max_tokens,
        # not reuse the same (already-exhausted) ceiling.
        original_call, repair_call = provider.calls
        self.assertGreater(repair_call["max_tokens"], original_call["max_tokens"])

    def test_original_truncated_repair_also_truncated_reports_honest_diagnostics(self):
        # Both the original AND the repair attempt hit max_tokens -- this
        # must fail honestly (ok=False), and the diagnostics must describe
        # the REPAIR attempt's own truncation, not silently carry over the
        # original failure's is_truncated value while reporting
        # repair_method=None (the pre-fix behavior: repair was never even
        # attempted because schema_description/max_tokens weren't wired for
        # SE Documentation's scoped rewrite/structural-repair calls).
        original = _FakeAnthropicResponse(
            _substantial_truncated_candidate(),
            stop_reason="max_tokens", output_tokens=14000,
        )
        repair_also_truncated = _FakeAnthropicResponse(
            '{"database": {"databaseEntities": [{"entityId": "ENT-01", "name": "User", "fields": [',
            stop_reason="max_tokens", output_tokens=21000,
        )
        provider = _ScriptedAnthropicProvider([original, repair_also_truncated])

        result = provider.generate_json("prompt", schema_description=_SCHEMA)

        self.assertFalse(result.ok)
        diagnostics = result.parse_diagnostics
        self.assertEqual(diagnostics["repairMethod"], "provider_repair")
        self.assertFalse(diagnostics["repairSuccess"])
        self.assertTrue(diagnostics["isTruncated"])  # describes the REPAIR output, not just carried over

    def test_original_truncated_repair_returns_complete_but_malformed_json(self):
        # The repair call succeeds (no exception, real text returned) but
        # that text is COMPLETE, not truncated -- just differently invalid.
        # isTruncated must be False here (it describes the repair output),
        # distinguishing this from the "repair also truncated" case above.
        original = _FakeAnthropicResponse(
            _substantial_truncated_candidate(),
            stop_reason="max_tokens", output_tokens=14000,
        )
        repair_malformed_not_truncated = _FakeAnthropicResponse(
            '{"database": {"databaseEntities": [{"entityId": "ENT-01" "name": "User"}]}}',
            stop_reason="end_turn", output_tokens=900,
        )
        provider = _ScriptedAnthropicProvider([original, repair_malformed_not_truncated])

        result = provider.generate_json("prompt", schema_description=_SCHEMA)

        self.assertFalse(result.ok)
        diagnostics = result.parse_diagnostics
        self.assertEqual(diagnostics["repairMethod"], "provider_repair")
        self.assertFalse(diagnostics["repairSuccess"])
        self.assertFalse(diagnostics["isTruncated"])

    def test_no_schema_description_never_attempts_repair(self):
        # Unchanged behavior: repair is opt-in via schema_description. A
        # caller that omits it (the pre-fix state for SE Documentation's
        # rewrite_targeted/fix_structure_scoped) gets no repair attempt at
        # all -- confirms this fix didn't change that contract.
        truncated = _FakeAnthropicResponse(
            _substantial_truncated_candidate(),
            stop_reason="max_tokens", output_tokens=14000,
        )
        provider = _ScriptedAnthropicProvider([truncated])

        result = provider.generate_json("prompt")  # no schema_description

        self.assertFalse(result.ok)
        self.assertEqual(len(provider.calls), 1)
        self.assertIsNone(result.parse_diagnostics["repairMethod"])

    def test_repair_provider_exception_never_raises_and_is_reported(self):
        original = _FakeAnthropicResponse(
            _substantial_truncated_candidate(),
            stop_reason="max_tokens", output_tokens=14000,
        )
        provider = _ScriptedAnthropicProvider([original, RuntimeError("connection reset")])

        result = provider.generate_json("prompt", schema_description=_SCHEMA)

        self.assertFalse(result.ok)
        diagnostics = result.parse_diagnostics
        self.assertEqual(diagnostics["repairMethod"], "provider_repair")
        self.assertFalse(diagnostics["repairSuccess"])

    def test_repair_max_tokens_sent_to_the_provider_has_headroom(self):
        original = _FakeAnthropicResponse(
            _substantial_truncated_candidate(),
            stop_reason="max_tokens", output_tokens=8000,
        )
        repaired = _FakeAnthropicResponse('{"database": {"databaseEntities": []}}')
        provider = _ScriptedAnthropicProvider([original, repaired])

        provider.generate_json("prompt", schema_description=_SCHEMA, max_tokens=8000)

        original_call, repair_call = provider.calls
        self.assertEqual(original_call["max_tokens"], 8000)
        self.assertEqual(repair_call["max_tokens"], 12000)  # _repair_max_tokens(8000)


if __name__ == "__main__":
    unittest.main()
